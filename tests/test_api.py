from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.services import AutomationService
from src.storage.db import create_automation_run, get_automation_run, upsert_job
from src.storage.models import JobRecord


def _wait_for_status(db_path: Path, run_id: int, status: str, timeout_s: float = 3.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = get_automation_run(run_id, db_path)
        if row is not None and row["status"] == status:
            return row
        time.sleep(0.05)
    return get_automation_run(run_id, db_path)


def test_api_health_and_jobs_list(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(
        JobRecord(
            workday_id="JR-api",
            title="Office Aide",
            location="Tempe campus",
            posting_date="04/24/2026",
            raw_description="Office aide role.",
            fit_score=91,
            fit_label="Strong Fit",
            recommended_resume_type="admin_office",
        ),
        db_path=db_path,
    )
    upsert_job(
        JobRecord(
            workday_id="JR-old",
            title="Desk Assistant",
            location="Tempe campus",
            posting_date="04/10/2026",
            raw_description="Desk assistant role.",
            fit_score=75,
            fit_label="Possible Fit",
            recommended_resume_type="admin_office",
        ),
        db_path=db_path,
    )
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        health = client.get("/api/health")
        jobs = client.get("/api/jobs", params={"q": "office"})
        dated_jobs = client.get(
            "/api/jobs",
            params={"posted_from": "2026-04-20", "posted_to": "2026-04-25"},
        )

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["workday_id"] == "JR-api"
    assert dated_jobs.status_code == 200
    assert [job["workday_id"] for job in dated_jobs.json()["jobs"]] == ["JR-api"]


def test_api_rejects_submit_without_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        response = client.post("/api/apply/job/1", json={"submit": True})

    assert response.status_code == 400
    assert "confirm_submit" in response.json()["detail"]


def test_api_continue_unblocks_waiting_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        run_id = service.submit(
            "login_capture",
            {},
            lambda context: context.wait_for_continue("Finish login in browser.", timeout_s=2)
            or {"continued": True},
        )
        waiting = _wait_for_status(db_path, run_id, "waiting_for_user")
        assert waiting is not None
        assert waiting["current_step"] == "Finish login in browser."

        response = client.post(f"/api/runs/{run_id}/continue")
        completed = _wait_for_status(db_path, run_id, "completed")

    assert response.status_code == 200
    assert completed is not None
    assert completed["status"] == "completed"
    assert '"continued":true' in completed["result_json"]


def test_api_startup_marks_stale_runs_interrupted(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    run_id = create_automation_run("scrape", {}, db_path=db_path, status="running")
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app):
        row = get_automation_run(run_id, db_path)

    assert row is not None
    assert row["status"] == "interrupted"
