from pathlib import Path

from src.apply_automation import AutoApplyResult
from src.apply_cli import main, next_job_id, open_job_url, render_apply_packet, render_queue
from src.storage.db import get_connection, get_job_by_id, list_apply_queue, upsert_job
from src.storage.models import JobRecord


def _job(
    workday_id: str,
    title: str,
    fit_score: int,
    fit_label: str,
    posting_date: str = "04/30/2026",
    status: str = "new",
    url: str | None = "https://www.myworkday.com/asu/job/JR-test",
) -> JobRecord:
    return JobRecord(
        workday_id=workday_id,
        title=title,
        location="Tempe campus",
        posting_date=posting_date,
        url=url,
        raw_description=f"{title} role.",
        fit_score=fit_score,
        fit_label=fit_label,
        job_family="office_admin",
        recommended_resume_type="admin_office",
        recommended_resume_name="Bharanidharan_Maheswaran_WP_Off_Ass.pdf",
        recommended_resume_path="resumes/master/Bharanidharan_Maheswaran_WP_Off_Ass.pdf",
        status=status,
    )


def test_apply_queue_excludes_done_statuses_and_orders_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-possible", "Possible Job", 99, "Possible Fit"), db_path)
    upsert_job(_job("JR-strong-low", "Strong Low", 80, "Strong Fit", "04/30/2026"), db_path)
    upsert_job(_job("JR-strong-high", "Strong High", 90, "Strong Fit", "04/29/2026"), db_path)
    upsert_job(_job("JR-applied", "Applied Job", 100, "Strong Fit", status="applied"), db_path)
    upsert_job(_job("JR-skipped", "Skipped Job", 100, "Strong Fit", status="skipped"), db_path)

    rows = list_apply_queue(db_path=db_path, limit=10)

    assert [row["workday_id"] for row in rows] == [
        "JR-strong-high",
        "JR-strong-low",
        "JR-possible",
    ]
    assert "Applied Job" not in render_queue(rows)
    assert "Skipped Job" not in render_queue(rows)


def test_next_job_id_returns_best_actionable_job(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-possible", "Possible Job", 99, "Possible Fit"), db_path)
    upsert_job(_job("JR-strong-low", "Strong Low", 80, "Strong Fit"), db_path)
    upsert_job(_job("JR-strong-high", "Strong High", 90, "Strong Fit"), db_path)

    job_id = next_job_id(db_path=db_path, min_score=80, fit_label="Strong Fit")
    row = get_job_by_id(job_id, db_path=db_path)

    assert row["workday_id"] == "JR-strong-high"


def test_apply_packet_includes_resume_path_and_workday_url(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(_job("JR-packet", "Office Aide", 88, "Strong Fit"), db_path)
    row = get_job_by_id(job_id, db_path=db_path)

    packet = render_apply_packet(row)

    assert "Apply Packet" in packet
    assert "Office Aide" in packet
    assert "resumes/master/Bharanidharan_Maheswaran_WP_Off_Ass.pdf" in packet
    assert "https://www.myworkday.com/asu/job/JR-test" in packet
    assert "--mark-applied" in packet


def test_status_commands_update_local_tracking_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(_job("JR-status", "Office Aide", 88, "Strong Fit"), db_path)

    assert main(["--mark-reviewing", str(job_id), "--db-path", str(db_path)]) == 0
    assert main(
        [
            "--mark-applied",
            str(job_id),
            "--note",
            "Submitted with office resume",
            "--db-path",
            str(db_path),
        ]
    ) == 0

    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT status, application_notes, applied_at, last_action_at
            FROM jobs
            WHERE id = ?;
            """,
            (job_id,),
        ).fetchone()

    assert row["status"] == "applied"
    assert row["application_notes"] == "Submitted with office resume"
    assert row["applied_at"]
    assert row["last_action_at"]


def test_mark_skipped_with_note_updates_status(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(_job("JR-skip", "Mascot", 55, "Skip"), db_path)

    assert main(
        [
            "--mark-skipped",
            str(job_id),
            "--note",
            "Not interested",
            "--db-path",
            str(db_path),
        ]
    ) == 0

    row = get_job_by_id(job_id, db_path=db_path)
    assert row["status"] == "skipped"
    assert row["application_notes"] == "Not interested"


def test_open_job_url_uses_browser_only_when_url_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    with_url_id = upsert_job(_job("JR-open", "Office Aide", 88, "Strong Fit"), db_path)
    no_url_id = upsert_job(_job("JR-no-url", "Manual Job", 70, "Possible Fit", url=None), db_path)
    opened_urls: list[str] = []

    def opener(url: str) -> bool:
        opened_urls.append(url)
        return True

    ok, message = open_job_url(with_url_id, db_path=db_path, opener=opener)

    assert ok is True
    assert opened_urls == ["https://www.myworkday.com/asu/job/JR-test"]
    assert "Opened Workday URL" in message

    ok, message = open_job_url(no_url_id, db_path=db_path, opener=opener)

    assert ok is True
    assert opened_urls == ["https://www.myworkday.com/asu/job/JR-test"]
    assert "Search Workday" in message


def test_auto_apply_cli_delegates_to_automation(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    debug_dir = tmp_path / "debug"
    calls = []

    def fake_auto_apply_job(
        job_id,
        db_path,
        auth_state_path,
        submit,
        headed,
        debug_dump_dir,
        timeout_ms,
        application_profile,
    ):
        calls.append(
            (
                job_id,
                db_path,
                auth_state_path,
                submit,
                headed,
                debug_dump_dir,
                timeout_ms,
                application_profile.applicant_name,
            )
        )
        return AutoApplyResult(job_id, True, True, False, "submitted")

    monkeypatch.setattr("src.apply_cli.auto_apply_job", fake_auto_apply_job)

    assert main(
        [
            "--auto-apply",
            "12",
            "--submit",
            "--headed",
            "--db-path",
            str(db_path),
            "--auth-state-path",
            str(auth_path),
            "--debug-dump-dir",
            str(debug_dir),
            "--click-timeout-ms",
            "1234",
        ]
    ) == 0

    assert calls == [(12, db_path, auth_path, True, True, debug_dir, 1234, "Bharanidharan Maheswaran")]


def test_auto_apply_next_chooses_next_best_job(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-possible", "Possible Job", 99, "Possible Fit"), db_path)
    strong_id = upsert_job(_job("JR-strong", "Strong Job", 90, "Strong Fit"), db_path)
    calls = []

    def fake_auto_apply_job(
        job_id,
        db_path,
        auth_state_path,
        submit,
        headed,
        debug_dump_dir,
        timeout_ms,
        application_profile,
    ):
        calls.append(job_id)
        return AutoApplyResult(job_id, True, False, True, "filled")

    monkeypatch.setattr("src.apply_cli.auto_apply_job", fake_auto_apply_job)

    assert main(["--auto-apply-next", "--db-path", str(db_path)]) == 0

    assert calls == [strong_id]
