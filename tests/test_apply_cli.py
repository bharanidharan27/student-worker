from pathlib import Path

from src.apply_automation import AutoApplyResult
from src.apply_cli import (
    extract_workday_id_from_url,
    find_job_id_by_url,
    main,
    next_job_id,
    open_job_url,
    parse_picker_choice,
    render_apply_packet,
    render_picker_menu,
    render_queue,
    run_picker,
)
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


def test_render_picker_menu_uses_one_based_numbers_not_db_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-zero-pad", "Padded First", 92, "Strong Fit"), db_path)
    upsert_job(_job("JR-second", "Second Job", 85, "Strong Fit"), db_path)

    rows = list_apply_queue(db_path=db_path, limit=10)
    menu = render_picker_menu(rows)

    assert "[1] Padded First" in menu
    assert "[2] Second Job" in menu
    # Database ids must NOT leak into the picker output.
    for row in rows:
        assert f"id: {row['id']}" not in menu
    assert "Type the number" in menu


def test_render_picker_menu_handles_empty_queue() -> None:
    assert "No actionable jobs" in render_picker_menu([])


def test_parse_picker_choice_accepts_digits_and_rejects_garbage() -> None:
    assert parse_picker_choice("1", 3) == 1
    assert parse_picker_choice(" 2 ", 3) == 2
    assert parse_picker_choice("#3", 3) == 3
    assert parse_picker_choice("", 3) is None
    assert parse_picker_choice("q", 3) is None
    assert parse_picker_choice("quit", 3) is None
    assert parse_picker_choice("abc", 3) is None
    assert parse_picker_choice("0", 3) is None
    assert parse_picker_choice("4", 3) is None


def test_pick_runs_auto_apply_for_chosen_job_without_typing_id(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-first", "First Job", 92, "Strong Fit"), db_path)
    second_id = upsert_job(_job("JR-second", "Second Job", 85, "Strong Fit"), db_path)
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
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")

    assert main(["--pick", "--db-path", str(db_path)]) == 0
    assert calls == [second_id]


def test_pick_quits_cleanly_when_user_types_q(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-only", "Only Job", 92, "Strong Fit"), db_path)

    def fail_auto_apply(*args, **kwargs):
        raise AssertionError("auto_apply_job must not run when user quits")

    monkeypatch.setattr("src.apply_cli.auto_apply_job", fail_auto_apply)
    monkeypatch.setattr("builtins.input", lambda prompt="": "q")

    assert main(["--pick", "--db-path", str(db_path)]) == 1


def test_extract_workday_id_from_url_handles_common_shapes() -> None:
    assert (
        extract_workday_id_from_url(
            "https://asu.wd1.myworkdayjobs.com/en-US/ASUStudentJobs/job/Tempe-Campus/Office-Aide_JR12345"
        )
        == "JR12345"
    )
    assert (
        extract_workday_id_from_url(
            "https://www.myworkday.com/asu/job/JR-99887"
        )
        == "JR-99887"
    )
    assert extract_workday_id_from_url("https://example.com/no-id-here") is None
    assert extract_workday_id_from_url("") is None


def test_find_job_id_by_url_matches_exact_url_then_workday_id(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        _job(
            "JR-99887",
            "Office Aide",
            90,
            "Strong Fit",
            url="https://asu.wd1.myworkdayjobs.com/en-US/ASUStudentJobs/job/Tempe/Office-Aide_JR-99887",
        ),
        db_path,
    )

    matched_id, message = find_job_id_by_url(
        "https://asu.wd1.myworkdayjobs.com/en-US/ASUStudentJobs/job/Tempe/Office-Aide_JR-99887",
        db_path=db_path,
    )
    assert matched_id == job_id
    assert "Matched" in message

    # Different URL string but same Workday id should still match.
    matched_id, message = find_job_id_by_url(
        "https://different.host/job/JR-99887?utm=campaign",
        db_path=db_path,
    )
    assert matched_id == job_id


def test_find_job_id_by_url_returns_none_when_no_match(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    upsert_job(_job("JR-other", "Other", 90, "Strong Fit"), db_path)

    matched_id, message = find_job_id_by_url(
        "https://asu.wd1.myworkdayjobs.com/job/Mystery_JR-00000",
        db_path=db_path,
    )

    assert matched_id is None
    assert "No saved job matched" in message


def test_auto_apply_url_runs_for_matched_job(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job_id = upsert_job(
        _job(
            "JR-12345",
            "Office Aide",
            90,
            "Strong Fit",
            url="https://asu.wd1.myworkdayjobs.com/en-US/ASUStudentJobs/job/Tempe/Office-Aide_JR-12345",
        ),
        db_path,
    )
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

    assert main(
        [
            "--auto-apply-url",
            "https://asu.wd1.myworkdayjobs.com/en-US/ASUStudentJobs/job/Tempe/Office-Aide_JR-12345",
            "--db-path",
            str(db_path),
        ]
    ) == 0
    assert calls == [job_id]


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
