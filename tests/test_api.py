from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.services import AutomationService
from src.auth.auth_meta import AuthMeta, write_auth_meta
from src.resume_tailoring import TailoredResumeResult
from src.storage.db import (
    create_automation_run,
    get_automation_run,
    update_automation_run,
    update_job_eligibility,
    upsert_job,
)
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
    api_job_id = upsert_job(
        JobRecord(
            workday_id="JR-api",
            title="Office Aide",
            location="Tempe campus",
            posting_date="04/24/2026",
            raw_description="Office aide role.",
            fit_score=91,
            fit_label="Strong Fit",
            recommended_resume_type="admin_office",
            eligibility_status="eligible",
            eligibility_json='{"status":"eligible","summary":"Looks good."}',
        ),
        db_path=db_path,
    )
    old_job_id = upsert_job(
        JobRecord(
            workday_id="JR-old",
            title="Desk Assistant",
            location="Tempe campus",
            posting_date="04/10/2026",
            raw_description="Desk assistant role.",
            fit_score=75,
            fit_label="Possible Fit",
            recommended_resume_type="admin_office",
            eligibility_status="needs_review",
            eligibility_json='{"status":"needs_review","summary":"Check hours."}',
        ),
        db_path=db_path,
    )
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)
    scrape_run_id = create_automation_run("scrape", {}, db_path=db_path, status="completed")
    update_automation_run(
        scrape_run_id,
        db_path,
        result={"job_ids": [old_job_id, api_job_id]},
        mark_finished=True,
    )

    with TestClient(app) as client:
        health = client.get("/api/health")
        jobs = client.get("/api/jobs", params={"q": "office"})
        dated_jobs = client.get(
            "/api/jobs",
            params={"posted_from": "2026-04-20", "posted_to": "2026-04-25"},
        )
        extracted_jobs = client.get("/api/jobs", params={"sort": "extracted"})
        posted_jobs = client.get("/api/jobs", params={"sort": "posted_desc"})
        eligible_jobs = client.get("/api/jobs", params={"eligibility_status": "eligible"})

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["workday_id"] == "JR-api"
    assert dated_jobs.status_code == 200
    assert [job["workday_id"] for job in dated_jobs.json()["jobs"]] == ["JR-api"]
    assert [job["workday_id"] for job in extracted_jobs.json()["jobs"][:2]] == ["JR-old", "JR-api"]
    assert [job["workday_id"] for job in posted_jobs.json()["jobs"][:2]] == ["JR-api", "JR-old"]
    assert [job["workday_id"] for job in eligible_jobs.json()["jobs"]] == ["JR-api"]
    assert jobs.json()["jobs"][0]["eligibility"]["summary"] == "Looks good."


def test_session_status_distinguishes_saved_file_from_authenticated_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        saved_file_status = client.get("/api/session/status", params={"auth_state_path": str(auth_path)})
        write_auth_meta(auth_path, AuthMeta(display_name="Bharanidharan Maheswaran", email="bharani@example.edu"))
        authenticated_status = client.get("/api/session/status", params={"auth_state_path": str(auth_path)})

    assert saved_file_status.status_code == 200
    assert saved_file_status.json()["exists"] is True
    assert saved_file_status.json()["authenticated"] is False
    assert saved_file_status.json()["display_name"] is None
    assert authenticated_status.status_code == 200
    assert authenticated_status.json()["authenticated"] is True
    assert authenticated_status.json()["display_name"] == "Bharanidharan Maheswaran"


def test_session_check_refreshes_profile_metadata(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    monkeypatch.setattr("src.api.app.check_session", lambda **_kwargs: True)

    def fake_refresh(**kwargs):
        write_auth_meta(
            Path(kwargs["auth_state_path"]),
            AuthMeta(display_name="Bharanidharan Maheswaran", email="bharani@example.edu"),
        )
        return AuthMeta(display_name="Bharanidharan Maheswaran", email="bharani@example.edu")

    monkeypatch.setattr("src.api.app.refresh_auth_meta_from_saved_session", fake_refresh)

    with TestClient(app) as client:
        response = client.post("/api/session/check", params={"auth_state_path": str(auth_path)})

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["authenticated"] is True
    assert response.json()["display_name"] == "Bharanidharan Maheswaran"


def test_extracted_sort_appends_prior_scrape_order_after_partial_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_ids = {}
    for workday_id, title in [
        ("JR-a", "Alpha"),
        ("JR-b", "Bravo"),
        ("JR-c", "Charlie"),
        ("JR-d", "Delta"),
    ]:
        job_ids[workday_id] = upsert_job(
            JobRecord(
                workday_id=workday_id,
                title=title,
                raw_description=f"{title} role.",
            ),
            db_path=db_path,
        )
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)
    full_run_id = create_automation_run("scrape", {}, db_path=db_path, status="completed")
    update_automation_run(
        full_run_id,
        db_path,
        result={
            "job_ids": [
                job_ids["JR-c"],
                job_ids["JR-a"],
                job_ids["JR-d"],
                job_ids["JR-b"],
            ]
        },
        mark_finished=True,
    )
    partial_run_id = create_automation_run("scrape", {}, db_path=db_path, status="completed")
    update_automation_run(
        partial_run_id,
        db_path,
        result={"job_ids": [job_ids["JR-c"], job_ids["JR-a"]]},
        mark_finished=True,
    )

    with TestClient(app) as client:
        extracted_jobs = client.get("/api/jobs", params={"sort": "extracted"})

    assert [job["workday_id"] for job in extracted_jobs.json()["jobs"][:4]] == [
        "JR-c",
        "JR-a",
        "JR-d",
        "JR-b",
    ]


def test_api_rejects_submit_without_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        response = client.post("/api/apply/job/1", json={"submit": True})

    assert response.status_code == 400
    assert "confirm_submit" in response.json()["detail"]


def test_api_unapply_returns_job_to_apply_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-requeue",
            title="Student Support Aide",
            raw_description="Support role.",
            fit_score=82,
            fit_label="Strong Fit",
        ),
        db_path=db_path,
    )
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        applied = client.patch(
            f"/api/jobs/{job_id}/status",
            json={"status": "applied", "note": "Submitted manually."},
        )
        queue_after_apply = client.get("/api/jobs", params={"queue": True})
        unapplied = client.patch(
            f"/api/jobs/{job_id}/status",
            json={"status": "new", "note": "Moved back to Apply queue."},
        )
        queue_after_unapply = client.get("/api/jobs", params={"queue": True})

    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert applied.json()["applied_at"]
    assert [job["id"] for job in queue_after_apply.json()["jobs"]] == []
    assert unapplied.status_code == 200
    assert unapplied.json()["status"] == "new"
    assert unapplied.json()["applied_at"] is None
    assert [job["id"] for job in queue_after_unapply.json()["jobs"]] == [job_id]


def test_api_eligibility_override_returns_job_to_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-ineligible",
            title="Undergraduate Peer Mentor",
            raw_description="Must be undergraduate.",
            fit_score=90,
            fit_label="Strong Fit",
            eligibility_status="ineligible",
            eligibility_json='{"status":"ineligible","summary":"Undergrad only."}',
        ),
        db_path=db_path,
    )
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        hidden = client.get("/api/jobs", params={"queue": True})
        override = client.patch(
            f"/api/jobs/{job_id}/eligibility-override",
            json={"eligibility_override": True, "note": "Reviewed manually."},
        )
        visible = client.get("/api/jobs", params={"queue": True})

    assert hidden.status_code == 200
    assert hidden.json()["jobs"] == []
    assert override.status_code == 200
    assert override.json()["eligibility_override"] is True
    assert [job["id"] for job in visible.json()["jobs"]] == [job_id]


def test_api_starts_selected_eligibility_review(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-review",
            title="Office Aide",
            raw_description="Current ASU student.",
        ),
        db_path=db_path,
    )

    def fake_review(job_id: int, db_path: Path):
        update_job_eligibility(job_id, "eligible", '{"status":"eligible","summary":"Reviewed."}', db_path)
        return SimpleNamespace(status="eligible", llm_used=False, provider=None, model=None)

    monkeypatch.setattr("src.api.app.review_stored_job_eligibility", fake_review)
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job_id}/eligibility/review", json={})
        completed = _wait_for_status(db_path, response.json()["id"], "completed")
        detail = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert completed is not None
    assert completed["status"] == "completed"
    assert detail.json()["eligibility_status"] == "eligible"
    assert detail.json()["eligibility"]["summary"] == "Reviewed."


def test_api_starts_all_jobs_eligibility_review(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(JobRecord(workday_id="JR-one", title="One", raw_description="One."), db_path=db_path)
    upsert_job(JobRecord(workday_id="JR-two", title="Two", raw_description="Two."), db_path=db_path)

    def fake_review_db(db_path: Path, progress=None):
        if progress is not None:
            progress(1, 2, 1)
            progress(2, 2, 2)
        return 2

    monkeypatch.setattr("src.api.app.review_db_eligibility", fake_review_db)
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        response = client.post("/api/eligibility/review", json={})
        completed = _wait_for_status(db_path, response.json()["id"], "completed")

    assert response.status_code == 200
    assert completed is not None
    assert completed["status"] == "completed"
    assert '"jobs_reviewed":2' in completed["result_json"]


def test_api_starts_tailor_resume_run(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-tailor",
            title="Technology Consultant",
            raw_description="Support Zoom.",
            recommended_resume_name="Base_Resume.pdf",
        ),
        db_path=db_path,
    )

    def fake_tailor(job_id: int, db_path: Path, extracted_dir=None, output_root=None):
        return TailoredResumeResult(
            job_id=job_id,
            job_title="Technology Consultant",
            source_resume_path="resumes/extracted/Base_Resume/main.tex",
            output_resume_path="resumes/tailored/1-technology-consultant/main.tex",
            output_dir="resumes/tailored/1-technology-consultant",
            notes_path="resumes/tailored/1-technology-consultant/tailoring_notes.md",
            generated_document_id=1,
            additions=["Experience with Zoom. Evidence: Zoom_Resume.docx."],
            skipped=[],
        )

    monkeypatch.setattr("src.api.app.tailor_resume_for_job", fake_tailor)
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job_id}/resume/tailor", json={})
        completed = _wait_for_status(db_path, response.json()["id"], "completed")

    assert response.status_code == 200
    assert completed is not None
    assert completed["status"] == "completed"
    assert '"output_resume_path":"resumes/tailored/1-technology-consultant/main.tex"' in completed["result_json"]


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


def test_api_continue_interrupts_stale_waiting_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        run_id = create_automation_run(
            "login_capture",
            {},
            db_path=db_path,
            status="waiting_for_user",
            current_step="Press Enter here after the jobs page loads...",
        )

        response = client.post(f"/api/runs/{run_id}/continue")
        interrupted = get_automation_run(run_id, db_path)
        events = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"] == "API server lost connection to this run. Start it again."
    assert "Continue requested, but this API process is no longer managing the run." in [
        event["message"] for event in events.json()["events"]
    ]


def test_api_stop_run_interrupts_active_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)
    started = threading.Event()

    def action(context):
        started.set()
        while True:
            context.raise_if_stopped()
            time.sleep(0.02)

    with TestClient(app) as client:
        run_id = service.submit("scrape", {}, action)
        assert started.wait(timeout=2)

        response = client.post(f"/api/runs/{run_id}/stop")
        interrupted = _wait_for_status(db_path, run_id, "interrupted")
        events = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"] == "Run stopped by user."
    assert "Stop requested from UI." in [event["message"] for event in events.json()["events"]]


def test_api_stop_interrupts_stale_active_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app) as client:
        run_id = create_automation_run(
            "scrape",
            {},
            db_path=db_path,
            status="running",
            current_step="Scraping Workday job listings.",
        )

        response = client.post(f"/api/runs/{run_id}/stop")
        interrupted = get_automation_run(run_id, db_path)
        events = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert interrupted is not None
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"] == "API server lost connection to this run. Start it again."
    assert "Stopped stale run record; no active worker was attached to this API process." in [
        event["message"] for event in events.json()["events"]
    ]


def test_api_startup_marks_stale_runs_interrupted(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    run_id = create_automation_run("scrape", {}, db_path=db_path, status="running")
    service = AutomationService(db_path)
    app = create_app(db_path=db_path, automation_service=service)

    with TestClient(app):
        row = get_automation_run(run_id, db_path)

    assert row is not None
    assert row["status"] == "interrupted"
