"""Local rule-based fit scoring."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.matching.keyword_extractor import count_term_hits
from src.matching.resume_selector import ResumeRecommendation, recommend_resume_for_job
from src.scraping.job_detail_parser import parse_job_description
from src.storage.db import DEFAULT_DB_PATH, get_connection, init_db
from src.storage.models import FitLabel, FitResult, ParsedJob


STRONG_FIT_THRESHOLD = 80
POSSIBLE_FIT_THRESHOLD = 60

TECH_FAMILIES = {
    "technical_assistant",
    "general_tech",
    "zoom_it",
    "product_ai",
    "data_tech",
    "research_lab",
}

TECHNICAL_PROFILE_TERMS = [
    "python",
    "java",
    "react",
    "sql",
    "database",
    "backend",
    "api",
    "software",
    "developer",
    "engineer",
    "machine learning",
    "ai",
    "automation",
    "docker",
    "aws",
    "kubernetes",
    "data analysis",
    "analytics",
    "technical",
]

ADMIN_PROFILE_TERMS = [
    "office",
    "administrative",
    "data entry",
    "records",
    "documentation",
    "email",
    "phone",
    "confidential",
    "operations",
    "microsoft office",
    "excel",
    "google workspace",
    "scheduling",
    "calendar",
]

SERVICE_PROFILE_TERMS = [
    "customer",
    "service",
    "support",
    "student",
    "ambassador",
    "peer",
    "front desk",
    "communication",
    "clerk",
    "admissions",
    "recruitment",
    "outreach",
]

COMMUNICATION_TERMS = [
    "communication",
    "customer",
    "support",
    "service",
    "documentation",
    "phone",
    "email",
    "team",
    "collaboration",
    "student",
    "front desk",
]

AVAILABILITY_LOCATION_TERMS = [
    "tempe",
    "downtown phoenix",
    "asu",
    "campus",
    "remote",
    "hybrid",
    "hours",
    "hour",
    "schedule",
]

EDUCATION_TERMS = [
    "student",
    "graduate",
    "computer science",
    "coursework",
    "degree",
    "asu",
    "ms",
    "master",
]

FAMILY_EXPERIENCE_TERMS: dict[str, list[str]] = {
    "office_admin": [
        "office",
        "administrative",
        "advising",
        "data entry",
        "records",
        "documentation",
        "email",
        "phone",
        "confidential",
        "microsoft office",
        "scheduling",
    ],
    "front_desk": [
        "front desk",
        "front office",
        "reception",
        "phone",
        "visitors",
        "appointments",
        "customer service",
        "student support",
    ],
    "operations_support": [
        "operations",
        "inventory",
        "equipment",
        "facilities",
        "mail",
        "event",
        "receiving",
        "support",
    ],
    "student_services": [
        "student success",
        "student services",
        "student support",
        "admissions",
        "recruitment",
        "outreach",
        "advising",
        "customer service",
        "communication",
    ],
    "finance_business": [
        "business office",
        "financial",
        "finance",
        "billing",
        "invoice",
        "reconcile",
        "procurement",
        "receiving",
        "spreadsheet",
        "excel",
    ],
    "business_hr": [
        "human resources",
        "hr",
        "hiring",
        "onboarding",
        "payroll",
        "employee",
        "confidential",
        "records",
        "office",
    ],
    "marketing_media": [
        "marketing",
        "social media",
        "content",
        "brand",
        "graphic",
        "design",
        "adobe",
        "video",
        "communication",
    ],
    "journalism_media": [
        "news",
        "journalism",
        "story",
        "copy",
        "editorial",
        "media",
        "communication",
    ],
    "research_lab": [
        "research",
        "laboratory",
        "lab",
        "data collection",
        "experiment",
        "participant",
        "protocol",
        "analysis",
        "documentation",
    ],
    "technical_assistant": [
        "technical",
        "technology",
        "programming",
        "python",
        "java",
        "sql",
        "api",
        "software",
        "automation",
        "documentation",
    ],
    "zoom_it": [
        "zoom",
        "it support",
        "troubleshoot",
        "ticket",
        "audio visual",
        "technical support",
        "customer service",
    ],
    "general_tech": [
        "software",
        "developer",
        "engineer",
        "programmer",
        "backend",
        "frontend",
        "react",
        "docker",
        "aws",
        "machine learning",
    ],
    "product_ai": [
        "ai",
        "artificial intelligence",
        "product",
        "requirements",
        "user stories",
        "qa",
        "testing",
        "feedback",
        "stakeholder",
        "analytics",
        "documentation",
    ],
    "data_tech": [
        "data",
        "data analysis",
        "database",
        "sql",
        "analytics",
        "dashboard",
        "reporting",
        "spreadsheet",
        "excel",
    ],
    "card_services": [
        "card services",
        "sun devil card",
        "id card",
        "customer service",
        "front desk",
        "student",
    ],
    "clerical_sbs": ["clerk", "clerical", "records", "filing", "data entry", "office"],
    "studio_support": ["studio", "equipment", "production", "media", "customer service"],
    "general_nontech": [
        "assistant",
        "aide",
        "office",
        "support",
        "customer service",
        "communication",
    ],
    "music_performance": ["quartet", "performer", "performance", "music", "instrument", "concert"],
}

FAMILY_KEYWORDS: dict[str, set[str]] = {
    "technical_assistant": {"python", "java", "sql", "api development", "automation", "documentation"},
    "general_tech": {"python", "java", "react", "sql", "docker", "aws", "machine learning"},
    "zoom_it": {"customer service", "communication", "documentation"},
    "product_ai": {"ai research", "product requirements", "user stories", "qa testing", "documentation"},
    "data_tech": {"sql", "data analysis", "microsoft office", "documentation"},
    "research_lab": {"data analysis", "documentation", "communication"},
    "office_admin": {"microsoft office", "google workspace", "data entry", "communication", "documentation"},
    "front_desk": {"front desk", "customer service", "communication", "microsoft office"},
    "operations_support": {"inventory", "customer service", "communication", "documentation"},
    "student_services": {"student support", "customer service", "communication", "documentation"},
    "finance_business": {"billing", "microsoft office", "data entry", "communication"},
    "business_hr": {"confidential data", "microsoft office", "data entry", "communication"},
    "marketing_media": {"communication", "documentation", "customer service"},
    "journalism_media": {"communication", "documentation"},
    "card_services": {"front desk", "customer service", "communication"},
}

CERTIFICATION_OR_LICENSE_TERMS = [
    "certification required",
    "certified",
    "driver's license",
    "drivers license",
    "food handler",
    "cpr",
    "first aid",
]

PHYSICAL_LABOR_TERMS = [
    "lift ",
    "lifting",
    "manual labor",
    "stand for",
    "cleaning",
    "move equipment",
    "moving equipment",
    "warehouse",
]

UNRELATED_SPECIALIZED_TERMS = [
    "lifeguard",
    "aquatic",
    "swim",
    "nursing",
    "medical assistant",
]


def _points_for_hits(hits: int, max_points: int, points_per_hit: int) -> int:
    return min(max_points, hits * points_per_hit)


def _label_for_score(score: int) -> FitLabel:
    if score >= STRONG_FIT_THRESHOLD:
        return "Strong Fit"
    if score >= POSSIBLE_FIT_THRESHOLD:
        return "Possible Fit"
    return "Skip"


def score_fit(parsed_job: ParsedJob, raw_description: str) -> FitResult:
    text = _combined_text(parsed_job, raw_description)
    recommendation = recommend_resume_for_job(parsed_job.title, text)
    family = recommendation.job_family

    family_score = _family_score(recommendation)
    family_terms = FAMILY_EXPERIENCE_TERMS.get(family, FAMILY_EXPERIENCE_TERMS["general_nontech"])
    family_hits = count_term_hits(text, family_terms)
    resume_evidence_score = _points_for_hits(family_hits, 30, 5)

    if family in TECH_FAMILIES:
        skill_hits = count_term_hits(text, TECHNICAL_PROFILE_TERMS)
        skill_score = _points_for_hits(skill_hits, 20, 4)
    else:
        admin_hits = count_term_hits(text, ADMIN_PROFILE_TERMS)
        service_hits = count_term_hits(text, SERVICE_PROFILE_TERMS)
        skill_hits = admin_hits + service_hits
        skill_score = min(20, admin_hits * 3 + service_hits * 3)

    communication_score = _points_for_hits(count_term_hits(text, COMMUNICATION_TERMS), 10, 2)
    availability_score = _points_for_hits(count_term_hits(text, AVAILABILITY_LOCATION_TERMS), 10, 5)
    education_score = _points_for_hits(count_term_hits(text, EDUCATION_TERMS), 10, 5)
    keyword_score = _family_keyword_score(parsed_job.keywords, family)

    score = min(
        100,
        family_score
        + resume_evidence_score
        + skill_score
        + communication_score
        + availability_score
        + education_score
        + keyword_score,
    )

    reasons = _build_reasons(
        recommendation=recommendation,
        family_score=family_score,
        resume_evidence_score=resume_evidence_score,
        skill_score=skill_score,
        communication_score=communication_score,
        availability_score=availability_score,
        education_score=education_score,
        keyword_score=keyword_score,
    )
    gaps = _build_gaps(
        family=family,
        family_score=family_score,
        resume_evidence_score=resume_evidence_score,
        skill_score=skill_score,
        availability_score=availability_score,
        education_score=education_score,
    )

    if score >= STRONG_FIT_THRESHOLD and (family_score < 16 or resume_evidence_score < 15):
        score = 79
        gaps.append("Strong fit was capped because family-specific evidence was thin.")

    capped_score, cap_reason = _apply_hard_caps(score, family, parsed_job.title, text)
    if capped_score < score:
        score = capped_score
        gaps.append(cap_reason)

    return FitResult(
        score=score,
        label=_label_for_score(score),
        reasons=reasons,
        gaps=gaps,
        job_family=family,
        recommended_resume_type=recommendation.recommended_resume_type,
        recommended_resume_name=recommendation.recommended_resume_name,
        recommended_resume_path=recommendation.recommended_resume_path,
    )


def rescore_db(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Recompute local fit fields for every stored job without scraping again."""

    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, department, location, raw_description
            FROM jobs
            ORDER BY id ASC;
            """
        ).fetchall()
        for row in rows:
            raw_description = row["raw_description"] or row["title"] or ""
            parsed_base = parse_job_description(raw_description)
            parsed = parsed_base.model_copy(
                update={
                    "title": row["title"] or parsed_base.title,
                    "department": row["department"] or parsed_base.department,
                    "location": row["location"] or parsed_base.location,
                }
            )
            fit = score_fit(parsed, raw_description)
            connection.execute(
                """
                UPDATE jobs
                SET
                  parsed_json = ?,
                  fit_score = ?,
                  fit_label = ?,
                  job_family = ?,
                  recommended_resume_type = ?,
                  recommended_resume_name = ?,
                  recommended_resume_path = ?,
                  updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (
                    parsed.model_dump_json(indent=2),
                    fit.score,
                    fit.label,
                    fit.job_family,
                    fit.recommended_resume_type,
                    fit.recommended_resume_name,
                    fit.recommended_resume_path,
                    row["id"],
                ),
            )
        connection.commit()
    return len(rows)


def _combined_text(parsed_job: ParsedJob, raw_description: str) -> str:
    return "\n".join(
        [
            parsed_job.title or "",
            parsed_job.department or "",
            parsed_job.location or "",
            parsed_job.pay_rate or "",
            parsed_job.hours or "",
            raw_description,
            " ".join(parsed_job.minimum_qualifications),
            " ".join(parsed_job.preferred_qualifications),
            " ".join(parsed_job.essential_duties),
            " ".join(parsed_job.required_skills),
            " ".join(parsed_job.keywords),
        ]
    ).lower()


def _family_score(recommendation: ResumeRecommendation) -> int:
    if recommendation.confidence <= 0:
        return 8
    return min(25, 8 + recommendation.confidence)


def _family_keyword_score(keywords: list[str], family: str) -> int:
    relevant = FAMILY_KEYWORDS.get(family, set())
    if not relevant:
        return 0
    hits = sum(1 for keyword in keywords if keyword.lower() in relevant)
    return _points_for_hits(hits, 10, 2)


def _build_reasons(
    recommendation: ResumeRecommendation,
    family_score: int,
    resume_evidence_score: int,
    skill_score: int,
    communication_score: int,
    availability_score: int,
    education_score: int,
    keyword_score: int,
) -> list[str]:
    reasons = [
        (
            f"Detected {recommendation.job_family} job family"
            f" from: {', '.join(recommendation.matched_terms) or 'general posting signals'}."
        ),
        (
            "Recommended master resume "
            f"{recommendation.recommended_resume_name}."
        ),
    ]
    if family_score:
        reasons.append(f"Job-family evidence contributed {family_score}/25 points.")
    if resume_evidence_score:
        reasons.append(f"Resume-specific evidence contributed {resume_evidence_score}/30 points.")
    if skill_score:
        reasons.append(f"Relevant skill evidence contributed {skill_score}/20 points.")
    if communication_score:
        reasons.append(f"Communication or support terms contributed {communication_score}/10 points.")
    if availability_score:
        reasons.append(f"Location, schedule, or ASU context contributed {availability_score}/10 points.")
    if education_score:
        reasons.append(f"Education or student context contributed {education_score}/10 points.")
    if keyword_score:
        reasons.append(f"Parsed keyword overlap contributed {keyword_score}/10 points.")
    return reasons


def _build_gaps(
    family: str,
    family_score: int,
    resume_evidence_score: int,
    skill_score: int,
    availability_score: int,
    education_score: int,
) -> list[str]:
    gaps: list[str] = []
    if family_score < 16:
        gaps.append("Only weak job-family evidence was found.")
    if resume_evidence_score < 15:
        gaps.append(f"Limited explicit match for the {family} resume family.")
    if skill_score < 8:
        gaps.append("Limited relevant skill overlap for the selected resume.")
    if availability_score == 0:
        gaps.append("Location, schedule, or hours were not clearly identified.")
    if education_score == 0:
        gaps.append("Student, degree, or coursework requirements were not clearly identified.")
    return gaps


def _apply_hard_caps(score: int, family: str, title: str | None, text: str) -> tuple[int, str]:
    title_text = (title or "").lower()
    role_intro = "\n".join(text.splitlines()[:20])
    if family == "music_performance":
        return min(score, 45), "Performance/music-only roles are capped because the resume catalog has no matching performance resume."
    if _has_role_specific_gap(title_text, title_text, UNRELATED_SPECIALIZED_TERMS):
        return min(score, 45), "Specialized role requirements appear outside the current resume evidence."
    if _has_role_specific_gap(title_text, role_intro, CERTIFICATION_OR_LICENSE_TERMS):
        return min(score, 58), "Role appears to require a certification or license not represented in the resume catalog."
    if count_term_hits(text, PHYSICAL_LABOR_TERMS):
        return min(score, 72), "Physical/manual-labor requirements capped the score."
    if "portfolio" in text and ("graphic design" in text or "adobe" in text):
        return min(score, 70), "Design-portfolio requirements capped the score."
    return score, ""


def _has_role_specific_gap(title_text: str, role_intro: str, terms: list[str]) -> bool:
    checked_text = f"{title_text}\n{role_intro}".lower()
    for term in terms:
        if _phrase_in_text(term, checked_text):
            return True
    return False


def _phrase_in_text(term: str, text: str) -> bool:
    if term.endswith(" "):
        return term in text
    return bool(re.search(rf"\b{re.escape(term.lower())}\b", text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score jobs and refresh resume recommendations.")
    parser.add_argument(
        "--rescore-db",
        action="store_true",
        help="Re-score all jobs currently stored in SQLite.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path.",
    )
    args = parser.parse_args(argv)

    if args.rescore_db:
        count = rescore_db(args.db_path)
        print(f"Rescored {count} job(s) in {args.db_path}.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
