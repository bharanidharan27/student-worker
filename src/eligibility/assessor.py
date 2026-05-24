"""Eligibility assessment using local rules with optional LLM enrichment."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from src.eligibility.llm import LLMJsonClient, get_llm_env_float
from src.eligibility.models import (
    EligibilityAssessment,
    JobRequirement,
    NonResumeAction,
    ResumeSuggestion,
)
from src.eligibility.profile import ApplicantProfile, load_applicant_profile
from src.scraping.job_detail_parser import parse_job_description
from src.storage.db import DEFAULT_DB_PATH, get_connection, get_job_by_id, update_job_eligibility
from src.storage.models import ParsedJob
from src.utils.text_cleaner import normalize_whitespace, sentence_split


TECH_TERMS = [
    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "React",
    "SQL",
    "Excel",
    "Microsoft Office",
    "Google Workspace",
    "Tableau",
    "Power BI",
    "AWS",
    "Docker",
    "Kubernetes",
    "Machine Learning",
    "AI",
    "API",
    "Canvas",
    "Zoom",
]

GENERIC_RESUME_SUGGESTION_TERMS = {
    "Excel",
    "Microsoft Office",
    "Google Workspace",
    "Microsoft Office Applications",
}

CERTIFICATION_TERMS = [
    "certification",
    "certified",
    "driver's license",
    "drivers license",
    "food handler",
    "cpr",
    "first aid",
    "lifeguard",
]

EXPERIENCE_PATTERNS = [
    r"(?:previous|prior|required)\s+experience\s+(?:in|with)\s+([^.;\n]+)",
    r"experience\s+(?:in|with)\s+([^.;\n]+)\s+(?:required|preferred)",
]


def assess_job_eligibility(
    parsed_job: ParsedJob,
    raw_description: str,
    *,
    profile: ApplicantProfile | None = None,
    llm_client: LLMJsonClient | None = None,
) -> EligibilityAssessment:
    """Assess eligibility and truthful resume/action gaps for one job."""

    applicant = profile or load_applicant_profile()
    local = _local_assessment(parsed_job, raw_description, applicant)
    client = llm_client or LLMJsonClient()
    if not client.available:
        return local

    try:
        llm_assessment = client.chat_json(
            system_prompt=_system_prompt(),
            user_prompt=_user_prompt(parsed_job, raw_description, applicant, local),
            response_model=EligibilityAssessment,
        )
    except Exception as exc:
        return local.model_copy(
            update={
                "provider": client.config.provider,
                "model": client.config.model,
            }
        )

    final = _merge_local_guards(local, llm_assessment)
    return final.model_copy(
        update={
            "llm_used": True,
            "provider": client.config.provider,
            "model": client.config.model,
        }
    )


def review_stored_job_eligibility(
    job_id: int,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    profile: ApplicantProfile | None = None,
    llm_client: LLMJsonClient | None = None,
) -> EligibilityAssessment:
    row = get_job_by_id(job_id, db_path=db_path)
    if row is None:
        raise ValueError(f"No job found with local id {job_id}.")

    raw_description = row["raw_description"] or row["title"] or ""
    parsed = _parsed_job_from_row(row, raw_description)
    assessment = assess_job_eligibility(
        parsed,
        raw_description,
        profile=profile,
        llm_client=llm_client,
    )
    update_job_eligibility(
        job_id,
        assessment.status,
        assessment.model_dump_json(indent=2),
        db_path=db_path,
    )
    return assessment


def review_db_eligibility(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    profile: ApplicantProfile | None = None,
    llm_client: LLMJsonClient | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> int:
    applicant_profile = profile or load_applicant_profile()
    client = llm_client or LLMJsonClient()
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM jobs
            ORDER BY id ASC;
            """
        ).fetchall()

    for index, row in enumerate(rows, start=1):
        job_id = int(row["id"])
        if progress is not None:
            progress(index, len(rows), job_id)
        if index > 1:
            _sleep_between_llm_reviews(client)
        review_stored_job_eligibility(
            job_id,
            db_path=db_path,
            profile=applicant_profile,
            llm_client=client,
        )
    return len(rows)


def _local_assessment(
    parsed_job: ParsedJob,
    raw_description: str,
    profile: ApplicantProfile,
) -> EligibilityAssessment:
    text = normalize_whitespace(raw_description)
    requirements = _extract_requirements(parsed_job, text)
    evaluated = [_evaluate_requirement(requirement, profile) for requirement in requirements]

    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[NonResumeAction] = []
    suggestions: list[ResumeSuggestion] = []

    for requirement in evaluated:
        if requirement.priority == "preferred" and requirement.match in {"missing", "unknown"}:
            warnings.append(f"Preferred: {requirement.text}")
            continue

        if requirement.match == "missing":
            if requirement.category in {"education", "certification"}:
                blockers.append(requirement.text)
                actions.append(
                    NonResumeAction(
                        action_type="do_not_apply",
                        description=f"Do not apply unless this requirement is actually satisfied: {requirement.text}",
                        priority="required",
                        source_quote=requirement.source_quote or None,
                    )
                )
            elif requirement.category == "work_study":
                warnings.append(f"Work-study requirement may not be met: {requirement.text}")
                actions.append(
                    NonResumeAction(
                        action_type="confirm_answer",
                        description="Confirm federal work-study eligibility before applying.",
                        priority="required",
                        source_quote=requirement.source_quote or None,
                    )
                )
            else:
                warnings.append(f"Required item needs review: {requirement.text}")
                actions.append(
                    NonResumeAction(
                        action_type="manual_review",
                        description=f"Confirm whether this requirement is truly satisfied: {requirement.text}",
                        priority="required",
                        source_quote=requirement.source_quote or None,
                    )
                )

        if requirement.match == "unknown" and requirement.priority == "must":
            warnings.append(f"Required item needs confirmation: {requirement.text}")
            actions.append(_action_for_unknown_requirement(requirement))

        if requirement.match == "met" and _should_suggest_resume_change(requirement, profile):
            suggestions.append(_resume_suggestion(requirement))

    status = _status_from_findings(blockers, warnings, evaluated)
    summary = _summary_for_status(status, blockers, warnings, suggestions)
    return EligibilityAssessment(
        status=status,
        summary=summary,
        requirements=evaluated,
        blockers=_unique(blockers),
        warnings=_unique(warnings),
        resume_suggestions=_unique_suggestions(suggestions),
        non_resume_actions=_unique_actions(actions),
        llm_used=False,
    )


def _parsed_job_from_row(row, raw_description: str) -> ParsedJob:
    parsed_json = row["parsed_json"]
    if parsed_json:
        try:
            parsed = ParsedJob.model_validate(json.loads(parsed_json))
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = parse_job_description(raw_description)
    else:
        parsed = parse_job_description(raw_description)

    return parsed.model_copy(
        update={
            "title": row["title"] or parsed.title,
            "department": row["department"] or parsed.department,
            "location": row["location"] or parsed.location,
            "pay_rate": row["pay_rate"] or parsed.pay_rate,
            "hours": row["hours"] or parsed.hours,
        }
    )


def _extract_requirements(parsed_job: ParsedJob, text: str) -> list[JobRequirement]:
    requirements: list[JobRequirement] = []
    minimum_lines = parsed_job.minimum_qualifications
    preferred_lines = parsed_job.preferred_qualifications

    for line in minimum_lines:
        requirements.append(_requirement(line, "must", _category_for_text(line), line, 0.85))
    for line in preferred_lines:
        requirements.append(_requirement(line, "preferred", _category_for_text(line), line, 0.8))

    requirements.extend(_education_requirements(text))
    requirements.extend(_hours_requirements(parsed_job.hours, text))
    requirements.extend(_technology_requirements(minimum_lines, preferred_lines, text))
    requirements.extend(_certification_requirements(text))
    requirements.extend(_experience_requirements(text))
    requirements.extend(_portfolio_requirements(text))
    requirements.extend(_work_study_requirements(text))

    return _dedupe_requirements(requirements)


def _education_requirements(text: str) -> list[JobRequirement]:
    requirements: list[JobRequirement] = []
    for sentence in sentence_split(text):
        lowered = sentence.lower()
        if "undergraduate" in lowered and _has_required_language(lowered):
            requirements.append(_requirement("Must be an undergraduate student.", "must", "education", sentence, 0.9))
        elif "graduate student" in lowered and _has_required_language(lowered):
            requirements.append(_requirement("Must be a graduate student.", "must", "education", sentence, 0.85))
        elif "current asu student" in lowered or "currently enrolled" in lowered:
            requirements.append(_requirement("Must be currently enrolled at ASU.", "must", "education", sentence, 0.8))
    return requirements


def _hours_requirements(hours: str | None, text: str) -> list[JobRequirement]:
    source_text = "\n".join([hours or "", text])
    requirements: list[JobRequirement] = []
    for sentence in sentence_split(source_text):
        lowered = sentence.lower()
        match = re.search(r"\b(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s+hours?\b", lowered)
        if not match:
            continue
        if not any(word in lowered for word in ("hour", "schedule", "available", "work")):
            continue
        requirement_text = f"Availability for {match.group(0)}."
        priority = "must" if _has_required_language(lowered) or "minimum" in lowered else "unknown"
        requirements.append(_requirement(requirement_text, priority, "availability", sentence, 0.75))
    return requirements


def _technology_requirements(
    minimum_lines: list[str],
    preferred_lines: list[str],
    text: str,
) -> list[JobRequirement]:
    requirements: list[JobRequirement] = []
    min_text = "\n".join(minimum_lines)
    pref_text = "\n".join(preferred_lines)
    for term in TECH_TERMS:
        if _contains_term(min_text, term):
            requirements.append(_requirement(f"Knowledge or experience with {term}.", "must", "technology", _quote_for_term(min_text, term), 0.8))
        elif _contains_term(pref_text, term):
            requirements.append(_requirement(f"Knowledge or experience with {term}.", "preferred", "technology", _quote_for_term(pref_text, term), 0.75))
        else:
            quote = _technology_requirement_quote(text, term)
            if quote:
                priority = "must" if _has_required_language(quote.lower()) else "unknown"
                requirements.append(_requirement(f"Knowledge or experience with {term}.", priority, "technology", quote, 0.65))
    return requirements


def _certification_requirements(text: str) -> list[JobRequirement]:
    requirements: list[JobRequirement] = []
    for term in CERTIFICATION_TERMS:
        quote = _quote_for_term(text, term)
        if quote and _has_required_language(quote.lower()):
            requirements.append(_requirement(f"Required certification or license: {term}.", "must", "certification", quote, 0.82))
    return requirements


def _experience_requirements(text: str) -> list[JobRequirement]:
    requirements: list[JobRequirement] = []
    for pattern in EXPERIENCE_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            phrase = normalize_whitespace(match.group(1)).strip(" .")
            if phrase:
                quote = _sentence_containing(text, phrase) or match.group(0)
                priority = "must" if _has_required_language(quote.lower()) else "preferred"
                requirements.append(_requirement(f"Experience with {phrase}.", priority, "experience", quote, 0.75))
    return requirements


def _portfolio_requirements(text: str) -> list[JobRequirement]:
    quote = _quote_for_term(text, "portfolio")
    if not quote:
        return []
    priority = "must" if _has_required_language(quote.lower()) else "preferred"
    return [_requirement("Portfolio may be required.", priority, "portfolio", quote, 0.75)]


def _work_study_requirements(text: str) -> list[JobRequirement]:
    quote = _quote_for_term(text, "federal work")
    if not quote:
        return []
    priority = "must" if _has_required_language(quote.lower()) else "unknown"
    return [_requirement("Federal work-study eligibility.", priority, "work_study", quote, 0.75)]


def _evaluate_requirement(requirement: JobRequirement, profile: ApplicantProfile) -> JobRequirement:
    text = requirement.text.lower()
    evidence: list[str] = []
    match = requirement.match
    notes = requirement.notes

    if requirement.category == "education":
        match, evidence, notes = _match_education(text, profile)
    elif requirement.category == "availability":
        match, evidence, notes = _match_availability(requirement, profile)
    elif requirement.category == "technology":
        match, evidence = _match_collection(text, profile.technologies)
    elif requirement.category == "experience":
        match, evidence = _match_collection(text, profile.experience_domains)
    elif requirement.category == "certification":
        match, evidence = _match_collection(text, profile.certifications)
    elif requirement.category == "work_study":
        if profile.federal_work_study is True:
            match, evidence = "met", ["Profile says federal work-study eligible."]
        elif profile.federal_work_study is False:
            match, evidence = "missing", ["Profile says federal work-study is not available."]
        else:
            match = "unknown"
    elif requirement.category == "work_authorization":
        match = "met" if profile.work_authorized else "missing"
    elif requirement.category == "portfolio":
        if profile.portfolio_links:
            match, evidence = "met", ["Profile includes portfolio link(s)."]
        else:
            match = "unknown" if requirement.priority != "must" else "missing"
    else:
        match = "unknown" if requirement.priority == "must" else "not_applicable"

    return requirement.model_copy(update={"match": match, "evidence": evidence, "notes": notes})


def _match_education(text: str, profile: ApplicantProfile) -> tuple[str, list[str], str | None]:
    level = profile.degree_level.lower()
    if "undergraduate" in text:
        if level in {"undergraduate", "bachelors", "bachelor", "undergrad"}:
            return "met", [f"Profile degree level: {profile.degree_level}."], None
        return "missing", [f"Profile degree level: {profile.degree_level}."], "Profile is not undergraduate."
    if "graduate" in text or "master" in text:
        if level in {"graduate", "masters", "master", "ms", "phd"}:
            return "met", [f"Profile degree level: {profile.degree_level}."], None
        return "missing", [f"Profile degree level: {profile.degree_level}."], "Profile is not graduate-level."
    if "asu" in text or "enrolled" in text or "student" in text:
        return (
            "met" if profile.enrolled_at_asu else "missing",
            ["Profile says currently enrolled at ASU." if profile.enrolled_at_asu else "Profile does not confirm ASU enrollment."],
            None,
        )
    return "unknown", [], None


def _match_availability(requirement: JobRequirement, profile: ApplicantProfile) -> tuple[str, list[str], str | None]:
    needed = _max_hours(requirement.source_quote or requirement.text)
    if needed is None:
        return "unknown", [], None
    if profile.available_hours_per_week is None:
        return "unknown", [], "Available hours are not set in the profile."
    evidence = [f"Profile available hours: {profile.available_hours_per_week}/week."]
    if profile.available_hours_per_week >= needed:
        return "met", evidence, None
    return "missing", evidence, f"Requirement appears to need up to {needed} hours/week."


def _match_collection(text: str, values: Iterable[str]) -> tuple[str, list[str]]:
    lowered_values = [value.lower() for value in values]
    for value, lowered in zip(values, lowered_values):
        if lowered and lowered in text:
            return "met", [f"Profile includes {value}."]
    return "missing", []


def _should_suggest_resume_change(requirement: JobRequirement, profile: ApplicantProfile) -> bool:
    if requirement.category not in {"technology", "experience"}:
        return False
    if requirement.match != "met":
        return False
    if requirement.category == "technology" and requirement.priority != "must":
        return False
    resume_text = " ".join(profile.resume_keywords).lower()
    if not resume_text:
        return False
    if _is_generic_resume_requirement(requirement) and requirement.priority != "must":
        return False
    return not any(_contains_term(resume_text, term) for term in _terms_from_requirement(requirement))


def _resume_suggestion(requirement: JobRequirement) -> ResumeSuggestion:
    evidence = "; ".join(requirement.evidence) or "Supported by the redacted applicant profile."
    return ResumeSuggestion(
        requirement=requirement.text,
        suggestion=f"Add or emphasize truthful evidence for: {requirement.text}",
        evidence=evidence,
        resume_section="Skills or Experience",
        priority="recommended",
    )


def _action_for_unknown_requirement(requirement: JobRequirement) -> NonResumeAction:
    if requirement.category == "availability":
        return NonResumeAction(
            action_type="confirm_availability",
            description=f"Confirm availability before applying: {requirement.text}",
            priority="required",
            source_quote=requirement.source_quote or None,
        )
    if requirement.category == "portfolio":
        return NonResumeAction(
            action_type="prepare_portfolio",
            description="Prepare or confirm portfolio materials before applying.",
            priority="recommended",
            source_quote=requirement.source_quote or None,
        )
    return NonResumeAction(
        action_type="manual_review",
        description=f"Confirm this requirement before applying: {requirement.text}",
        priority="required",
        source_quote=requirement.source_quote or None,
    )


def _status_from_findings(
    blockers: list[str],
    warnings: list[str],
    requirements: list[JobRequirement],
) -> str:
    if blockers:
        return "ineligible"
    if warnings:
        if all(warning.startswith("Preferred:") for warning in warnings):
            return "eligible"
        return "needs_review"
    if any(requirement.match == "unknown" and requirement.priority == "must" for requirement in requirements):
        return "needs_review"
    return "eligible"


def _summary_for_status(
    status: str,
    blockers: list[str],
    warnings: list[str],
    suggestions: list[ResumeSuggestion],
) -> str:
    if status == "ineligible":
        return f"Likely ineligible because of: {blockers[0]}"
    if status == "needs_review":
        return "Review required before applying; some required facts are missing or uncertain."
    if suggestions:
        return "Eligible based on local profile; resume can be strengthened with truthful evidence."
    return "Eligible based on local profile and extracted requirements."


def _merge_local_guards(local: EligibilityAssessment, llm_assessment: EligibilityAssessment) -> EligibilityAssessment:
    if local.status == "ineligible" and llm_assessment.status != "ineligible":
        llm_assessment = llm_assessment.model_copy(
            update={
                "status": "ineligible",
                "summary": local.summary,
                "blockers": _unique(llm_assessment.blockers + local.blockers),
                "non_resume_actions": _unique_actions(llm_assessment.non_resume_actions + local.non_resume_actions),
            }
        )
    elif local.status == "needs_review" and llm_assessment.status == "eligible":
        llm_assessment = llm_assessment.model_copy(
            update={
                "status": "needs_review",
                "warnings": _unique(llm_assessment.warnings + local.warnings),
                "non_resume_actions": _unique_actions(llm_assessment.non_resume_actions + local.non_resume_actions),
            }
        )
    return llm_assessment


def _system_prompt() -> str:
    return (
        "You assess student job eligibility. Return only JSON matching the schema. "
        "Never suggest fabricating experience, enrollment status, hours, certifications, or technologies. "
        "Resume suggestions must be truthful and based only on the redacted profile facts."
    )


def _user_prompt(
    parsed_job: ParsedJob,
    raw_description: str,
    profile: ApplicantProfile,
    local: EligibilityAssessment,
) -> str:
    return "\n\n".join(
        [
            "Assess this job against the redacted applicant profile.",
            f"Parsed job JSON:\n{parsed_job.model_dump_json(indent=2)}",
            f"Redacted profile JSON:\n{profile.model_dump_json(indent=2)}",
            f"Local rule baseline JSON:\n{local.model_dump_json(indent=2)}",
            f"Raw public job posting:\n{raw_description}",
        ]
    )


def _requirement(
    text: str,
    priority: str,
    category: str,
    source_quote: str,
    confidence: float,
) -> JobRequirement:
    return JobRequirement(
        text=normalize_whitespace(text).strip(" .") + ".",
        priority=priority,
        category=category,
        source_quote=normalize_whitespace(source_quote),
        confidence=confidence,
    )


def _category_for_text(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("undergraduate", "graduate", "enrolled", "student", "degree", "asu")):
        return "education"
    if "hour" in lowered or "schedule" in lowered or "availability" in lowered:
        return "availability"
    if any(term.lower() in lowered for term in TECH_TERMS):
        return "technology"
    if any(term in lowered for term in CERTIFICATION_TERMS):
        return "certification"
    if "work study" in lowered or "work-study" in lowered:
        return "work_study"
    if "portfolio" in lowered:
        return "portfolio"
    if "experience" in lowered:
        return "experience"
    return "other"


def _has_required_language(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "must",
            "required",
            "minimum",
            "need to",
            "needs to",
            "eligible",
            "only",
            "currently enrolled",
            "should be able",
        )
    )


def _technology_requirement_quote(text: str, term: str) -> str | None:
    quote = _quote_for_term(text, term)
    if not quote:
        return None
    lowered = quote.lower()
    if any(word in lowered for word in ("experience", "knowledge", "proficient", "required", "preferred", "skill")):
        return quote
    return None


def _quote_for_term(text: str, term: str) -> str:
    return _sentence_containing(text, term) or ""


def _sentence_containing(text: str, term: str) -> str | None:
    for sentence in sentence_split(text):
        if _contains_term(sentence, term):
            return normalize_whitespace(sentence)
    for line in normalize_whitespace(text).splitlines():
        if _contains_term(line, term):
            return normalize_whitespace(line)
    return None


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if term.lower() in {"ai", "hr"}:
        return bool(re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE))
    return term.lower() in text.lower()


def _max_hours(text: str) -> int | None:
    matches = re.findall(r"\b(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s+hours?\b", text, flags=re.IGNORECASE)
    if not matches:
        return None
    return max(int(high or low) for low, high in matches)


def _terms_from_requirement(requirement: JobRequirement) -> list[str]:
    return [
        term
        for term in TECH_TERMS + list(requirement.text.split())
        if _contains_term(requirement.text, term)
    ]


def _is_generic_resume_requirement(requirement: JobRequirement) -> bool:
    return any(_contains_term(requirement.text, term) for term in GENERIC_RESUME_SUGGESTION_TERMS)


def _sleep_between_llm_reviews(client: LLMJsonClient) -> None:
    if not client.available:
        return
    delay_seconds = get_llm_env_float("LLM_REVIEW_DELAY_SECONDS", 1.0)
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def _dedupe_requirements(requirements: list[JobRequirement]) -> list[JobRequirement]:
    result: list[JobRequirement] = []
    seen: set[tuple[str, str, str]] = set()
    for requirement in requirements:
        key = (requirement.text.lower(), requirement.priority, requirement.category)
        if key in seen:
            continue
        seen.add(key)
        result.append(requirement)
    return result


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_suggestions(values: list[ResumeSuggestion]) -> list[ResumeSuggestion]:
    seen: set[str] = set()
    result: list[ResumeSuggestion] = []
    for value in values:
        key = _resume_suggestion_key(value)
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _resume_suggestion_key(value: ResumeSuggestion) -> str:
    if any(_contains_term(value.requirement, term) for term in GENERIC_RESUME_SUGGESTION_TERMS):
        return "generic-office-tools"
    return f"{value.requirement}|{value.suggestion}"


def _unique_actions(values: list[NonResumeAction]) -> list[NonResumeAction]:
    seen: set[str] = set()
    result: list[NonResumeAction] = []
    for value in values:
        key = f"{value.action_type}|{value.description}"
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result
