"""FastAPI app for the local React operational console."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.schemas import (
    ApplyJobRequest,
    ApplyQueueRequest,
    AutomationRunEventsResponse,
    AutomationRunListResponse,
    AutomationRunLogResponse,
    AutomationRunResponse,
    ContinueRunResponse,
    HealthResponse,
    JobListResponse,
    JobResponse,
    ScrapeRequest,
    SessionCheckResponse,
    SessionStatusResponse,
    StartLoginCaptureRequest,
    UpdateJobStatusRequest,
)
from src.api.services import AutomationService, RunContext
from src.apply_automation import (
    ApplicationProfile,
    auto_apply_job,
    auto_apply_queue,
    _run_playwright_apply,
)
from src.auth.login_capture import DEFAULT_AUTH_STATE_PATH, DEFAULT_WORKDAY_URL, capture_login_state
from src.auth.session_check import auth_state_exists, check_session
from src.scraping.workday_scraper import scrape_workday_jobs
from src.storage.db import (
    APPLY_QUEUE_EXCLUDED_STATUSES,
    DEFAULT_DB_PATH,
    JOB_LIST_COLUMNS_SQL,
    POSTING_DATE_SORT_SQL,
    get_automation_run,
    get_connection,
    get_job_by_id,
    init_db,
    list_automation_run_logs,
    list_automation_runs,
    update_job_status,
)


LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def create_app(
    db_path: Path = DEFAULT_DB_PATH,
    automation_service: AutomationService | None = None,
) -> FastAPI:
    service = automation_service or AutomationService(db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(db_path)
        service.startup()
        try:
            yield
        finally:
            service.shutdown()

    app = FastAPI(title="Student Work Applier API", version="0.1.0", lifespan=lifespan)
    app.state.automation_service = service
    app.state.db_path = db_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if client_host not in LOCAL_CLIENT_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"detail": "This API only accepts local browser requests."},
            )
        return await call_next(request)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/session/status", response_model=SessionStatusResponse)
    def session_status(auth_state_path: str | None = None) -> SessionStatusResponse:
        return _session_status_response(_path_or_default(auth_state_path, DEFAULT_AUTH_STATE_PATH))

    @app.post("/api/session/check", response_model=SessionCheckResponse)
    def session_check(auth_state_path: str | None = None, url: str | None = None) -> SessionCheckResponse:
        auth_path = _path_or_default(auth_state_path, DEFAULT_AUTH_STATE_PATH)
        status = _session_status_response(auth_path)
        if not status.exists:
            return SessionCheckResponse(
                **status.model_dump(),
                valid=False,
                message="Saved Workday session is missing. Start login capture first.",
            )
        try:
            valid = check_session(
                workday_url=url or DEFAULT_WORKDAY_URL,
                auth_state_path=auth_path,
                headless=True,
            )
        except RuntimeError as exc:
            return SessionCheckResponse(**status.model_dump(), valid=False, message=str(exc))
        message = (
            "Saved Workday session looks valid."
            if valid
            else "Saved Workday session is missing or expired. Run login capture again."
        )
        return SessionCheckResponse(**_session_status_response(auth_path).model_dump(), valid=valid, message=message)

    @app.post("/api/session/capture/start", response_model=AutomationRunResponse)
    def start_login_capture(body: StartLoginCaptureRequest) -> AutomationRunResponse:
        params = body.model_dump()

        def action(context: RunContext) -> dict[str, Any]:
            saved_path = capture_login_state(
                workday_url=body.url or DEFAULT_WORKDAY_URL,
                auth_state_path=_path_or_default(body.auth_state_path, DEFAULT_AUTH_STATE_PATH),
                browser_name=body.browser,
                slow_mo_ms=body.slow_mo_ms,
                wait_for_user=context.wait_for_continue,
            )
            return {"auth_state_path": str(saved_path), "message": "Saved Workday session state."}

        run_id = service.submit("login_capture", params, action)
        return _run_or_404(run_id, db_path)

    @app.post("/api/runs/{run_id}/continue", response_model=ContinueRunResponse)
    def continue_run(run_id: int) -> ContinueRunResponse:
        if not service.continue_run(run_id):
            raise HTTPException(status_code=404, detail=f"No run found with id {run_id}.")
        return ContinueRunResponse(accepted=True, run=_run_or_404(run_id, db_path))

    @app.get("/api/runs", response_model=AutomationRunListResponse)
    def runs(limit: int = Query(default=50, ge=1, le=500)) -> AutomationRunListResponse:
        return AutomationRunListResponse(
            runs=[_run_response_from_row(row) for row in list_automation_runs(db_path, limit=limit)]
        )

    @app.get("/api/runs/{run_id}", response_model=AutomationRunResponse)
    def run_detail(run_id: int) -> AutomationRunResponse:
        return _run_or_404(run_id, db_path)

    @app.get("/api/runs/{run_id}/events", response_model=AutomationRunEventsResponse)
    def run_events(
        run_id: int,
        after_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=500, ge=1, le=2_000),
    ) -> AutomationRunEventsResponse:
        if get_automation_run(run_id, db_path) is None:
            raise HTTPException(status_code=404, detail=f"No run found with id {run_id}.")
        rows = list_automation_run_logs(run_id, db_path, after_id=after_id, limit=limit)
        return AutomationRunEventsResponse(events=[_log_response_from_row(row) for row in rows])

    @app.post("/api/scrapes", response_model=AutomationRunResponse)
    def start_scrape(body: ScrapeRequest) -> AutomationRunResponse:
        params = body.model_dump()

        def action(context: RunContext) -> dict[str, Any]:
            context.set_step("Scraping Workday job listings.")
            summary = scrape_workday_jobs(
                workday_url=body.url or DEFAULT_WORKDAY_URL,
                auth_state_path=_path_or_default(body.auth_state_path, DEFAULT_AUTH_STATE_PATH),
                db_path=_path_or_default(body.db_path, db_path),
                limit=body.limit,
                headless=not body.headed,
                wait_ms=body.wait_ms,
                max_scrolls=body.max_scrolls,
                idle_rounds=body.idle_rounds,
                click_timeout_ms=body.click_timeout_ms,
                debug_dump_dir=Path(body.debug_dump_dir) if body.debug_dump_dir else None,
            )
            return asdict(summary)

        run_id = service.submit("scrape", params, action)
        return _run_or_404(run_id, db_path)

    @get_jobs_route(app, db_path)
    def _jobs_route(
        q: str | None = Query(default=None),
        status: str | None = Query(default=None),
        fit_label: str | None = Query(default=None),
        min_score: int | None = Query(default=None, ge=0, le=100),
        queue: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> JobListResponse:
        rows = _query_jobs(db_path, q, status, fit_label, min_score, queue, limit)
        return JobListResponse(jobs=[_job_response_from_row(row, include_description=False) for row in rows])

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def job_detail(job_id: int) -> JobResponse:
        row = get_job_by_id(job_id, db_path=db_path)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No job found with id {job_id}.")
        return _job_response_from_row(row, include_description=True)

    @app.patch("/api/jobs/{job_id}/status", response_model=JobResponse)
    def patch_job_status(job_id: int, body: UpdateJobStatusRequest) -> JobResponse:
        try:
            updated = update_job_status(job_id, body.status, note=body.note, db_path=db_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail=f"No job found with id {job_id}.")
        row = get_job_by_id(job_id, db_path=db_path)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No job found with id {job_id}.")
        return _job_response_from_row(row, include_description=True)

    @app.post("/api/apply/job/{job_id}", response_model=AutomationRunResponse)
    def start_apply_job(job_id: int, body: ApplyJobRequest) -> AutomationRunResponse:
        _enforce_submit_confirmation(body.submit, body.confirm_submit)
        params = body.model_dump() | {"job_id": job_id}

        def action(context: RunContext) -> dict[str, Any]:
            context.set_step(f"Applying to job {job_id}.")
            result = auto_apply_job(
                job_id,
                db_path=_path_or_default(body.db_path, db_path),
                auth_state_path=_path_or_default(body.auth_state_path, DEFAULT_AUTH_STATE_PATH),
                submit=body.submit,
                headed=body.headed,
                debug_dump_dir=Path(body.debug_dump_dir) if body.debug_dump_dir else None,
                timeout_ms=body.click_timeout_ms,
                application_profile=ApplicationProfile(applicant_name=body.applicant_name),
                driver=_api_apply_driver(context),
            )
            return asdict(result)

        run_id = service.submit("apply_job", params, action)
        return _run_or_404(run_id, db_path)

    @app.post("/api/apply/queue", response_model=AutomationRunResponse)
    def start_apply_queue(body: ApplyQueueRequest) -> AutomationRunResponse:
        _enforce_submit_confirmation(body.submit, body.confirm_submit)
        params = body.model_dump()

        def action(context: RunContext) -> list[dict[str, Any]]:
            context.set_step("Applying to ranked queue.")
            results = auto_apply_queue(
                db_path=_path_or_default(body.db_path, db_path),
                auth_state_path=_path_or_default(body.auth_state_path, DEFAULT_AUTH_STATE_PATH),
                limit=body.limit,
                min_score=body.min_score,
                fit_label=body.fit_label,
                submit=body.submit,
                headed=body.headed,
                debug_dump_dir=Path(body.debug_dump_dir) if body.debug_dump_dir else None,
                timeout_ms=body.click_timeout_ms,
                application_profile=ApplicationProfile(applicant_name=body.applicant_name),
                driver=_api_apply_driver(context),
            )
            return [asdict(result) for result in results]

        run_id = service.submit("apply_queue", params, action)
        return _run_or_404(run_id, db_path)

    return app


def get_jobs_route(app: FastAPI, db_path: Path):
    """Small wrapper to keep the decorated job-list endpoint readable."""

    return app.get("/api/jobs", response_model=JobListResponse)


def _api_apply_driver(context: RunContext):
    def driver(job, submit, headed, debug_dump_dir, auth_state_path, timeout_ms, profile):
        return _run_playwright_apply(
            job,
            submit,
            headed,
            debug_dump_dir,
            auth_state_path,
            timeout_ms,
            profile,
            review_input_func=context.review_input,
        )

    return driver


def _enforce_submit_confirmation(submit: bool, confirm_submit: bool) -> None:
    if submit and not confirm_submit:
        raise HTTPException(
            status_code=400,
            detail="Final Workday submit requires confirm_submit=true.",
        )


def _path_or_default(value: str | None, default: Path) -> Path:
    return Path(value) if value else default


def _session_status_response(auth_state_path: Path) -> SessionStatusResponse:
    exists = auth_state_exists(auth_state_path)
    size_bytes = auth_state_path.stat().st_size if exists else 0
    modified_at = (
        datetime.fromtimestamp(auth_state_path.stat().st_mtime).isoformat(timespec="seconds")
        if exists
        else None
    )
    return SessionStatusResponse(
        auth_state_path=str(auth_state_path),
        exists=exists,
        size_bytes=size_bytes,
        modified_at=modified_at,
    )


def _run_or_404(run_id: int, db_path: Path) -> AutomationRunResponse:
    row = get_automation_run(run_id, db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run found with id {run_id}.")
    return _run_response_from_row(row)


def _run_response_from_row(row) -> AutomationRunResponse:
    return AutomationRunResponse(
        id=int(row["id"]),
        kind=row["kind"],
        status=row["status"],
        params=_json_loads(row["params_json"]) or {},
        result=_json_loads(row["result_json"]),
        current_step=row["current_step"],
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _log_response_from_row(row) -> AutomationRunLogResponse:
    return AutomationRunLogResponse(
        id=int(row["id"]),
        run_id=int(row["run_id"]),
        level=row["level"],
        message=row["message"],
        created_at=row["created_at"],
    )


def _query_jobs(
    db_path: Path,
    q: str | None,
    status: str | None,
    fit_label: str | None,
    min_score: int | None,
    queue: bool,
    limit: int,
):
    init_db(db_path)
    where = ["1 = 1"]
    values: list[object] = []
    if queue:
        where.append("COALESCE(status, 'new') NOT IN (?, ?)")
        values.extend(APPLY_QUEUE_EXCLUDED_STATUSES)
    if status:
        where.append("COALESCE(status, 'new') = ?")
        values.append(status)
    if fit_label:
        where.append("fit_label = ?")
        values.append(fit_label)
    if min_score is not None:
        where.append("COALESCE(fit_score, 0) >= ?")
        values.append(min_score)
    if q:
        like = f"%{q.lower()}%"
        where.append(
            """
            lower(
              COALESCE(title, '') || ' ' ||
              COALESCE(workday_id, '') || ' ' ||
              COALESCE(location, '') || ' ' ||
              COALESCE(department, '') || ' ' ||
              COALESCE(recommended_resume_name, '')
            ) LIKE ?
            """
        )
        values.append(like)
    values.append(limit)
    with get_connection(db_path) as connection:
        return connection.execute(
            f"""
            SELECT {JOB_LIST_COLUMNS_SQL}
            FROM jobs
            WHERE {" AND ".join(where)}
            ORDER BY
              CASE fit_label
                WHEN 'Strong Fit' THEN 0
                WHEN 'Possible Fit' THEN 1
                ELSE 2
              END ASC,
              COALESCE(fit_score, 0) DESC,
              {POSTING_DATE_SORT_SQL} DESC,
              id ASC
            LIMIT ?;
            """,
            values,
        ).fetchall()


def _job_response_from_row(row, include_description: bool) -> JobResponse:
    parsed = _json_loads(row["parsed_json"])
    return JobResponse(
        id=int(row["id"]),
        workday_id=row["workday_id"],
        title=row["title"] or "Untitled job",
        department=row["department"],
        location=row["location"],
        pay_rate=row["pay_rate"],
        hours=row["hours"],
        posting_date=row["posting_date"],
        deadline=row["deadline"],
        url=row["url"],
        raw_description=row["raw_description"] if include_description else None,
        parsed=parsed if isinstance(parsed, dict) else None,
        fit_score=row["fit_score"],
        fit_label=row["fit_label"],
        job_family=row["job_family"],
        recommended_resume_type=row["recommended_resume_type"],
        recommended_resume_name=row["recommended_resume_name"],
        recommended_resume_path=row["recommended_resume_path"],
        status=row["status"],
        application_notes=row["application_notes"],
        applied_at=row["applied_at"],
        last_action_at=row["last_action_at"],
    )


def _json_loads(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


app = create_app()
