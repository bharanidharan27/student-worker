import sqlite3
from pathlib import Path

from src.storage.db import (
    count_rows,
    get_connection,
    init_db,
    list_jobs,
    list_tables,
    update_job_status,
    upsert_job,
)
from src.storage.models import JobRecord


def test_init_db_creates_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"

    init_db(db_path)

    assert {"jobs", "generated_documents"}.issubset(list_tables(db_path))


def test_upsert_job_deduplicates_by_workday_id(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job = JobRecord(
        workday_id="manual-test",
        title="Software Assistant",
        raw_description="Python software assistant role.",
        fit_score=80,
        fit_label="Strong Fit",
        job_family="technical_assistant",
        recommended_resume_type="technical",
        recommended_resume_name="Bharanidharan_M_PartTime_Tech_Ass.pdf",
        recommended_resume_path="resumes/master/Bharanidharan_M_PartTime_Tech_Ass.pdf",
    )

    first_id = upsert_job(job, db_path)
    second_id = upsert_job(job.model_copy(update={"title": "Updated Software Assistant"}), db_path)

    assert first_id == second_id
    assert count_rows("jobs", db_path) == 1


def test_list_jobs_returns_recent_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job = JobRecord(
        workday_id="JR120138",
        title="Advising Office Aide",
        location="Tempe",
        posting_date="04/24/2026",
        raw_description="Advising office aide role.",
    )

    upsert_job(job, db_path)
    rows = list_jobs(db_path, limit=1)

    assert len(rows) == 1
    assert rows[0]["workday_id"] == "JR120138"
    assert rows[0]["title"] == "Advising Office Aide"


def test_upsert_preserves_existing_application_status(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job = JobRecord(
        workday_id="JR-applied",
        title="Office Aide",
        raw_description="Office aide role.",
    )

    job_id = upsert_job(job, db_path)
    assert update_job_status(job_id, "applied", "Submitted manually.", db_path)
    upsert_job(job.model_copy(update={"title": "Updated Office Aide"}), db_path)

    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT status, application_notes FROM jobs WHERE id = ?;",
            (job_id,),
        ).fetchone()

    assert row["status"] == "applied"
    assert row["application_notes"] == "Submitted manually."


def test_init_db_migrates_old_jobs_table_without_losing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "old_jobs.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workday_id TEXT UNIQUE,
              title TEXT NOT NULL,
              department TEXT,
              location TEXT,
              pay_rate TEXT,
              hours TEXT,
              posting_date TEXT,
              deadline TEXT,
              url TEXT,
              raw_description TEXT,
              parsed_json TEXT,
              fit_score INTEGER,
              fit_label TEXT,
              recommended_resume_type TEXT,
              status TEXT DEFAULT 'new',
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        connection.execute(
            """
            INSERT INTO jobs (workday_id, title, raw_description, recommended_resume_type)
            VALUES ('JR-old', 'Office Assistant', 'Office assistant role.', 'admin_office');
            """
        )
        connection.commit()

    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(jobs);").fetchall()
        }
        row_count = connection.execute("SELECT COUNT(*) FROM jobs;").fetchone()[0]

    assert {
        "job_family",
        "recommended_resume_name",
        "recommended_resume_path",
        "application_notes",
        "applied_at",
        "last_action_at",
    }.issubset(columns)
    assert row_count == 1
