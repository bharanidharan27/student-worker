"""Pydantic models for job eligibility and resume-gap advice."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EligibilityStatus = Literal["eligible", "needs_review", "ineligible"]
RequirementPriority = Literal["must", "preferred", "unknown"]
RequirementCategory = Literal[
    "education",
    "availability",
    "technology",
    "experience",
    "certification",
    "work_authorization",
    "work_study",
    "location",
    "portfolio",
    "other",
]
RequirementMatch = Literal["met", "missing", "unknown", "not_applicable"]
ActionPriority = Literal["required", "recommended", "optional"]
ActionType = Literal[
    "confirm_availability",
    "confirm_answer",
    "prepare_portfolio",
    "prepare_transcript",
    "obtain_certification",
    "manual_review",
    "do_not_apply",
    "other",
]


class JobRequirement(BaseModel):
    """A requirement or preference extracted from a posting."""

    model_config = ConfigDict(extra="forbid")

    text: str
    priority: RequirementPriority = "unknown"
    category: RequirementCategory = "other"
    source_quote: str = ""
    confidence: float = Field(default=0.6, ge=0, le=1)
    match: RequirementMatch = "unknown"
    evidence: list[str] = Field(default_factory=list)
    notes: str | None = None


class ResumeSuggestion(BaseModel):
    """A truthful resume change to consider for the selected resume."""

    model_config = ConfigDict(extra="forbid")

    requirement: str
    suggestion: str
    evidence: str
    resume_section: str | None = None
    priority: ActionPriority = "recommended"


class NonResumeAction(BaseModel):
    """Action outside the resume needed before applying."""

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    description: str
    priority: ActionPriority = "recommended"
    source_quote: str | None = None


class EligibilityAssessment(BaseModel):
    """Validated eligibility decision and review artifacts."""

    model_config = ConfigDict(extra="forbid")

    status: EligibilityStatus
    summary: str
    requirements: list[JobRequirement] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resume_suggestions: list[ResumeSuggestion] = Field(default_factory=list)
    non_resume_actions: list[NonResumeAction] = Field(default_factory=list)
    llm_used: bool = False
    provider: str | None = None
    model: str | None = None
