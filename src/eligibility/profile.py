"""Applicant profile loading for redacted eligibility checks."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_PROFILE_PATH = Path("data/applicant_profile.yaml")


class ApplicantProfile(BaseModel):
    """Non-contact facts the advisor may use for eligibility decisions."""

    model_config = ConfigDict(extra="forbid")

    degree_level: str = "masters"
    program: str = "Computer Science"
    enrolled_at_asu: bool = True
    available_hours_per_week: int | None = None
    federal_work_study: bool | None = False
    work_authorized: bool | None = True
    age_18_or_older: bool | None = True
    preferred_locations: list[str] = Field(default_factory=lambda: ["Tempe", "Remote", "Hybrid"])
    technologies: list[str] = Field(
        default_factory=lambda: [
            "Python",
            "Java",
            "React",
            "SQL",
            "API",
            "Automation",
            "Machine Learning",
            "Data Analysis",
            "Microsoft Office",
            "Excel",
            "Google Workspace",
        ]
    )
    experience_domains: list[str] = Field(
        default_factory=lambda: [
            "software development",
            "technical support",
            "data analysis",
            "documentation",
            "student support",
            "office administration",
            "customer service",
        ]
    )
    certifications: list[str] = Field(default_factory=list)
    portfolio_links: list[str] = Field(default_factory=list)
    resume_keywords: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)


def load_applicant_profile(path: Path = DEFAULT_PROFILE_PATH) -> ApplicantProfile:
    """Load a redacted local profile, returning safe defaults when missing."""

    if not path.exists():
        return ApplicantProfile()

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Applicant profile must be a mapping: {path}")
    return ApplicantProfile.model_validate(data)
