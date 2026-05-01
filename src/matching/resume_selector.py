"""Select the best master resume for a job posting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.matching.resume_catalog import ResumeCatalogEntry, catalog_entry_for_family
from src.storage.models import ResumeType


@dataclass(frozen=True)
class FamilyRule:
    title_terms: tuple[str, ...] = ()
    description_terms: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class ResumeRecommendation:
    job_family: str
    recommended_resume_type: ResumeType
    recommended_resume_name: str
    recommended_resume_path: str
    confidence: int
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


FAMILY_RULES: dict[str, FamilyRule] = {
    "music_performance": FamilyRule(
        title_terms=(
            "quartet",
            "performer",
            "music",
            "musician",
            "instrument",
            "violin",
            "cello",
            "ensemble",
        ),
        description_terms=("performance", "perform", "concert", "music", "instrument"),
        priority=8,
    ),
    "product_ai": FamilyRule(
        title_terms=("ai product", "product assistant", "product"),
        description_terms=(
            "ai",
            "artificial intelligence",
            "product",
            "prd",
            "requirements",
            "user stories",
            "user story",
            "qa",
            "testing",
            "feedback",
            "stakeholder",
            "analytics",
        ),
        priority=6,
    ),
    "zoom_it": FamilyRule(
        title_terms=("zoom", "it support", "technology support", "av support", "audio visual"),
        description_terms=("zoom", "audio visual", "a/v", "it support", "troubleshoot", "ticket"),
        priority=5,
    ),
    "data_tech": FamilyRule(
        title_terms=("data aide", "data assistant", "data desk", "data"),
        description_terms=("data analysis", "database", "sql", "analytics", "dashboard", "reporting"),
        priority=5,
    ),
    "research_lab": FamilyRule(
        title_terms=(
            "research aide",
            "research assistant",
            "research laboratory",
            "laboratory aide",
            "lab aide",
            "behavioral laboratory",
            "legal research",
        ),
        description_terms=(
            "research",
            "laboratory",
            "lab",
            "data collection",
            "experiment",
            "participant",
            "protocol",
            "irb",
            "qualitative",
            "quantitative",
        ),
        priority=5,
    ),
    "technical_assistant": FamilyRule(
        title_terms=(
            "technical",
            "technology student aide",
            "instructional design",
            "programming technician",
            "course operations",
            "software developer",
            "developer assistant",
        ),
        description_terms=(
            "technical",
            "programming",
            "python",
            "java",
            "sql",
            "api",
            "developer",
            "software",
            "automation",
            "instructional technology",
            "canvas",
        ),
        priority=4,
    ),
    "general_tech": FamilyRule(
        title_terms=("software", "developer", "engineer", "programmer", "systems"),
        description_terms=("backend", "frontend", "react", "docker", "aws", "kubernetes", "machine learning"),
        priority=3,
    ),
    "business_hr": FamilyRule(
        title_terms=("human resources", "hr assistant", "hr aide", "hr"),
        description_terms=("human resources", "hiring", "onboarding", "payroll", "employee", "hris", "confidential"),
        priority=7,
    ),
    "finance_business": FamilyRule(
        title_terms=(
            "business office assistant",
            "business office",
            "financial office",
            "financial",
            "finance",
            "accounting",
            "receiving assistant",
            "supply chain",
            "student mail",
        ),
        description_terms=(
            "billing",
            "invoice",
            "reconcile",
            "financial",
            "procurement",
            "receiving",
            "supply chain",
            "budget",
            "spreadsheet",
        ),
        priority=5,
    ),
    "student_services": FamilyRule(
        title_terms=(
            "student success",
            "student services",
            "student service",
            "admissions",
            "recruitment",
            "outreach",
            "ambassador",
            "peer",
            "program assistant",
            "curriculum assistant",
        ),
        description_terms=(
            "student support",
            "advising",
            "admissions",
            "recruitment",
            "student success",
            "outreach",
            "programming",
            "customer service",
        ),
        priority=5,
    ),
    "front_desk": FamilyRule(
        title_terms=("front desk", "front office", "reception", "desk aide", "desk assistant"),
        description_terms=("front desk", "reception", "phone", "visitors", "appointments", "customer service"),
        priority=4,
    ),
    "office_admin": FamilyRule(
        title_terms=(
            "advising office",
            "office assistant",
            "office aide",
            "front office support",
            "administrative assistant",
            "administrative",
            "records",
        ),
        description_terms=(
            "office",
            "clerical",
            "data entry",
            "records",
            "email",
            "phone",
            "documentation",
            "calendar",
            "scheduling",
            "confidential",
            "microsoft office",
        ),
        priority=4,
    ),
    "operations_support": FamilyRule(
        title_terms=(
            "facilities",
            "building manager",
            "operations",
            "operator",
            "events attendant",
            "event",
            "mail",
        ),
        description_terms=("inventory", "equipment", "facilities", "mail", "event setup", "operations", "support"),
        priority=3,
    ),
    "card_services": FamilyRule(
        title_terms=("card aide", "sun devil card", "card services"),
        description_terms=("card services", "sun devil card", "id card", "customer service"),
        priority=5,
    ),
    "clerical_sbs": FamilyRule(
        title_terms=("sbs clerk", "clerk"),
        description_terms=("clerical", "records", "filing", "data entry", "office"),
        priority=3,
    ),
    "journalism_media": FamilyRule(
        title_terms=("news assistant", "cronkite", "journalism"),
        description_terms=("news", "journalism", "story", "copy", "editorial", "media"),
        priority=5,
    ),
    "studio_support": FamilyRule(
        title_terms=("studio associate", "studio assistant"),
        description_terms=("studio", "equipment", "production", "media", "customer service"),
        priority=4,
    ),
    "marketing_media": FamilyRule(
        title_terms=(
            "marketing",
            "social media",
            "brand assistant",
            "graphic design",
            "graphic",
            "digital culture",
        ),
        description_terms=("marketing", "social media", "content", "brand", "graphic", "design", "adobe", "video"),
        priority=4,
    ),
}


TIE_BREAK_ORDER = [
    "music_performance",
    "product_ai",
    "zoom_it",
    "data_tech",
    "research_lab",
    "technical_assistant",
    "general_tech",
    "business_hr",
    "finance_business",
    "student_services",
    "front_desk",
    "office_admin",
    "card_services",
    "operations_support",
    "journalism_media",
    "studio_support",
    "marketing_media",
    "clerical_sbs",
]


def recommend_resume_for_job(title: str | None, description: str | None) -> ResumeRecommendation:
    title_text = title or ""
    description_text = description or ""
    best_family = "general_nontech"
    best_score = 0
    best_matches: tuple[str, ...] = ()

    for family in TIE_BREAK_ORDER:
        rule = FAMILY_RULES[family]
        title_matches = _matched_terms(title_text, rule.title_terms)
        description_matches = _matched_terms(description_text, rule.description_terms)
        score = _family_score_from_matches(
            family=family,
            title_matches=title_matches,
            description_matches=description_matches,
            priority=rule.priority,
        )

        if score > best_score:
            best_family = family
            best_score = score
            best_matches = tuple(dict.fromkeys(title_matches + description_matches))

    entry = catalog_entry_for_family(best_family)
    return _recommendation(entry, best_score, best_matches)


def select_resume_type(title: str | None, description: str) -> ResumeType:
    """Return the broad resume type for older callers."""

    return recommend_resume_for_job(title, description).recommended_resume_type


def _recommendation(
    entry: ResumeCatalogEntry,
    confidence: int,
    matched_terms: tuple[str, ...],
) -> ResumeRecommendation:
    return ResumeRecommendation(
        job_family=entry.job_family,
        recommended_resume_type=entry.resume_type,
        recommended_resume_name=entry.resume_name,
        recommended_resume_path=entry.resume_path,
        confidence=confidence,
        matched_terms=matched_terms,
    )


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _term_in_text(term, text)]


def _family_score_from_matches(
    family: str,
    title_matches: list[str],
    description_matches: list[str],
    priority: int,
) -> int:
    if title_matches:
        return priority + len(title_matches) * 10 + len(description_matches)

    if not _allow_description_only_family(family, description_matches):
        return 0

    return priority + len(description_matches) * 2


def _allow_description_only_family(family: str, description_matches: list[str]) -> bool:
    if len(description_matches) < 3:
        return False
    if family == "business_hr":
        return False
    if family == "product_ai":
        return "ai" in description_matches and (
            "product" in description_matches
            or "requirements" in description_matches
            or "user stories" in description_matches
            or "testing" in description_matches
        )
    if family in {"marketing_media", "journalism_media", "studio_support"}:
        return len(description_matches) >= 4
    return True


def _term_in_text(term: str, text: str) -> bool:
    lowered_text = text.lower()
    lowered_term = term.lower()
    if lowered_term.isalnum() and len(lowered_term) <= 3:
        return bool(re.search(rf"\b{re.escape(lowered_term)}\b", lowered_text))
    return lowered_term in lowered_text
