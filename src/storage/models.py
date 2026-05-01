"""Pydantic models used by the local assistant."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ResumeType = Literal["technical", "product_ai", "admin_office", "customer_service"]
FitLabel = Literal["Strong Fit", "Possible Fit", "Skip"]
DocumentType = Literal["resume", "cover_letter", "skills", "report"]
ApplicationStatus = Literal["new", "reviewing", "applied", "skipped"]


class ParsedJob(BaseModel):
    """Structured fields extracted from a raw job posting."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    department: str | None = None
    pay_rate: str | None = None
    hours: str | None = None
    location: str | None = None
    minimum_qualifications: list[str] = Field(default_factory=list)
    preferred_qualifications: list[str] = Field(default_factory=list)
    essential_duties: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    software_tools: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class FitResult(BaseModel):
    """Rule-based fit score and recommendation."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    label: FitLabel
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    job_family: str | None = None
    recommended_resume_type: ResumeType
    recommended_resume_name: str | None = None
    recommended_resume_path: str | None = None


class JobRecord(BaseModel):
    """SQLite job row representation."""

    model_config = ConfigDict(extra="forbid")

    workday_id: str
    title: str
    department: str | None = None
    location: str | None = None
    pay_rate: str | None = None
    hours: str | None = None
    posting_date: str | None = None
    deadline: str | None = None
    url: str | None = None
    raw_description: str
    parsed_json: str | None = None
    fit_score: int | None = None
    fit_label: FitLabel | None = None
    job_family: str | None = None
    recommended_resume_type: ResumeType | None = None
    recommended_resume_name: str | None = None
    recommended_resume_path: str | None = None
    status: ApplicationStatus = "new"
    application_notes: str | None = None
    applied_at: str | None = None
    last_action_at: str | None = None


class StoredJob(JobRecord):
    """Job row with database metadata."""

    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GeneratedDocumentRecord(BaseModel):
    """SQLite generated document row representation."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    document_type: DocumentType
    file_path: str


class StoredGeneratedDocument(GeneratedDocumentRecord):
    """Generated document row with database metadata."""

    id: int
    created_at: datetime | None = None
