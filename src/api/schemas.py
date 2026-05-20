"""Pydantic request and response models for the dashboard API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.storage.models import ApplicationStatus


RunKind = Literal["login_capture", "scrape", "apply_job", "apply_queue"]
RunStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "completed",
    "failed",
    "interrupted",
]


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "student-work-applier-api"


class SessionStatusResponse(BaseModel):
    auth_state_path: str
    exists: bool
    size_bytes: int = 0
    modified_at: str | None = None


class SessionCheckResponse(SessionStatusResponse):
    valid: bool
    message: str


class StartLoginCaptureRequest(BaseModel):
    url: str | None = None
    auth_state_path: str | None = None
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    slow_mo_ms: int = Field(default=0, ge=0, le=5_000)


class ScrapeRequest(BaseModel):
    url: str | None = None
    auth_state_path: str | None = None
    db_path: str | None = None
    limit: int | None = Field(default=10, ge=1, le=500)
    headed: bool = False
    wait_ms: int = Field(default=750, ge=0, le=10_000)
    max_scrolls: int = Field(default=50, ge=1, le=250)
    idle_rounds: int = Field(default=3, ge=1, le=25)
    click_timeout_ms: int = Field(default=5_000, ge=500, le=60_000)
    debug_dump_dir: str | None = None


class ApplyJobRequest(BaseModel):
    submit: bool = False
    confirm_submit: bool = False
    headed: bool = True
    auth_state_path: str | None = None
    db_path: str | None = None
    debug_dump_dir: str | None = None
    click_timeout_ms: int = Field(default=10_000, ge=500, le=120_000)
    applicant_name: str = "Bharanidharan Maheswaran"


class ApplyQueueRequest(ApplyJobRequest):
    limit: int = Field(default=3, ge=1, le=25)
    min_score: int = Field(default=70, ge=0, le=100)
    fit_label: str = ""


class UpdateJobStatusRequest(BaseModel):
    status: ApplicationStatus
    note: str | None = None


class AutomationRunResponse(BaseModel):
    id: int
    kind: str
    status: RunStatus
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | list[Any] | None = None
    current_step: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AutomationRunListResponse(BaseModel):
    runs: list[AutomationRunResponse]


class AutomationRunLogResponse(BaseModel):
    id: int
    run_id: int
    level: str
    message: str
    created_at: str | None = None


class AutomationRunEventsResponse(BaseModel):
    events: list[AutomationRunLogResponse]


class ContinueRunResponse(BaseModel):
    accepted: bool
    run: AutomationRunResponse


class JobResponse(BaseModel):
    id: int
    workday_id: str | None = None
    title: str
    department: str | None = None
    location: str | None = None
    pay_rate: str | None = None
    hours: str | None = None
    posting_date: str | None = None
    deadline: str | None = None
    url: str | None = None
    raw_description: str | None = None
    parsed: dict[str, Any] | None = None
    fit_score: int | None = None
    fit_label: str | None = None
    job_family: str | None = None
    recommended_resume_type: str | None = None
    recommended_resume_name: str | None = None
    recommended_resume_path: str | None = None
    status: str | None = None
    application_notes: str | None = None
    applied_at: str | None = None
    last_action_at: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
