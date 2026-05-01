"""SQLite setup and persistence helpers."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

from src.storage.models import GeneratedDocumentRecord, JobRecord


DEFAULT_DB_PATH = Path("data/jobs.sqlite")
APPLY_QUEUE_EXCLUDED_STATUSES = ("applied", "skipped")
VALID_APPLICATION_STATUSES = {"new", "reviewing", "applied", "skipped"}

JOB_LIST_COLUMNS_SQL = """
  id,
  workday_id,
  title,
  department,
  location,
  pay_rate,
  hours,
  posting_date,
  deadline,
  url,
  raw_description,
  parsed_json,
  fit_score,
  fit_label,
  job_family,
  recommended_resume_type,
  recommended_resume_name,
  recommended_resume_path,
  status,
  application_notes,
  applied_at,
  last_action_at
"""

POSTING_DATE_SORT_SQL = """
CASE
  WHEN posting_date GLOB '??/??/????'
  THEN substr(posting_date, 7, 4) || '-' || substr(posting_date, 1, 2) || '-' || substr(posting_date, 4, 2)
  ELSE ''
END
"""


JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
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
  job_family TEXT,
  recommended_resume_type TEXT,
  recommended_resume_name TEXT,
  recommended_resume_path TEXT,
  status TEXT DEFAULT 'new',
  application_notes TEXT,
  applied_at TIMESTAMP,
  last_action_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


GENERATED_DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS generated_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  document_type TEXT,
  file_path TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


JOBS_COLUMN_MIGRATIONS = {
    "job_family": "ALTER TABLE jobs ADD COLUMN job_family TEXT;",
    "recommended_resume_name": "ALTER TABLE jobs ADD COLUMN recommended_resume_name TEXT;",
    "recommended_resume_path": "ALTER TABLE jobs ADD COLUMN recommended_resume_path TEXT;",
    "application_notes": "ALTER TABLE jobs ADD COLUMN application_notes TEXT;",
    "applied_at": "ALTER TABLE jobs ADD COLUMN applied_at TIMESTAMP;",
    "last_action_at": "ALTER TABLE jobs ADD COLUMN last_action_at TIMESTAMP;",
}


def ensure_database_dir(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    ensure_database_dir(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute(JOBS_TABLE_SQL)
        connection.execute(GENERATED_DOCUMENTS_TABLE_SQL)
        _migrate_jobs_table(connection)
        connection.commit()


def _migrate_jobs_table(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(jobs);").fetchall()
    }
    for column_name, migration_sql in JOBS_COLUMN_MIGRATIONS.items():
        if column_name not in columns:
            connection.execute(migration_sql)


def list_tables(db_path: Path = DEFAULT_DB_PATH) -> set[str]:
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table';"
        ).fetchall()
    return {row["name"] for row in rows}


def upsert_job(job: JobRecord, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Insert or update a job using workday_id as the duplicate key."""

    init_db(db_path)
    values = job.model_dump()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO jobs (
              workday_id, title, department, location, pay_rate, hours,
              posting_date, deadline, url, raw_description, parsed_json,
              fit_score, fit_label, job_family, recommended_resume_type,
              recommended_resume_name, recommended_resume_path, status,
              application_notes, applied_at, last_action_at
            ) VALUES (
              :workday_id, :title, :department, :location, :pay_rate, :hours,
              :posting_date, :deadline, :url, :raw_description, :parsed_json,
              :fit_score, :fit_label, :job_family, :recommended_resume_type,
              :recommended_resume_name, :recommended_resume_path, :status,
              :application_notes, :applied_at, :last_action_at
            )
            ON CONFLICT(workday_id) DO UPDATE SET
              title = excluded.title,
              department = excluded.department,
              location = excluded.location,
              pay_rate = excluded.pay_rate,
              hours = excluded.hours,
              posting_date = excluded.posting_date,
              deadline = excluded.deadline,
              url = excluded.url,
              raw_description = excluded.raw_description,
              parsed_json = excluded.parsed_json,
              fit_score = excluded.fit_score,
              fit_label = excluded.fit_label,
              job_family = excluded.job_family,
              recommended_resume_type = excluded.recommended_resume_type,
              recommended_resume_name = excluded.recommended_resume_name,
              recommended_resume_path = excluded.recommended_resume_path,
              status = CASE
                WHEN jobs.status IN ('reviewing', 'applied', 'skipped') AND excluded.status = 'new'
                THEN jobs.status
                ELSE excluded.status
              END,
              application_notes = COALESCE(excluded.application_notes, jobs.application_notes),
              applied_at = COALESCE(excluded.applied_at, jobs.applied_at),
              last_action_at = COALESCE(excluded.last_action_at, jobs.last_action_at),
              updated_at = CURRENT_TIMESTAMP;
            """,
            values,
        )
        row = connection.execute(
            "SELECT id FROM jobs WHERE workday_id = ?;",
            (job.workday_id,),
        ).fetchone()
        connection.commit()
    if row is None:
        raise RuntimeError(f"Failed to upsert job {job.workday_id}")
    return int(row["id"])


def insert_generated_document(
    document: GeneratedDocumentRecord,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    init_db(db_path)
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO generated_documents (job_id, document_type, file_path)
            VALUES (:job_id, :document_type, :file_path);
            """,
            document.model_dump(),
        )
        connection.commit()
    return int(cursor.lastrowid)


def count_rows(table: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    if table not in {"jobs", "generated_documents"}:
        raise ValueError(f"Unsupported table: {table}")
    with get_connection(db_path) as connection:
        row = connection.execute(f"SELECT COUNT(*) AS count FROM {table};").fetchone()
    return int(row["count"])


def list_jobs(db_path: Path = DEFAULT_DB_PATH, limit: int = 10) -> list[sqlite3.Row]:
    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT {JOB_LIST_COLUMNS_SQL}
            FROM jobs
            ORDER BY id ASC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()
    return rows


def list_apply_queue(db_path: Path = DEFAULT_DB_PATH, limit: int = 10) -> list[sqlite3.Row]:
    """Return actionable jobs, ranked for manual review and applying."""

    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT {JOB_LIST_COLUMNS_SQL}
            FROM jobs
            WHERE COALESCE(status, 'new') NOT IN (?, ?)
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
            (*APPLY_QUEUE_EXCLUDED_STATUSES, limit),
        ).fetchall()
    return rows


def get_job_by_id(job_id: int, db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Row | None:
    init_db(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            f"""
            SELECT {JOB_LIST_COLUMNS_SQL}
            FROM jobs
            WHERE id = ?;
            """,
            (job_id,),
        ).fetchone()
    return row


def update_job_status(
    job_id: int,
    status: str,
    note: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    if status not in VALID_APPLICATION_STATUSES:
        allowed = ", ".join(sorted(VALID_APPLICATION_STATUSES))
        raise ValueError(f"Unsupported status {status!r}. Expected one of: {allowed}.")

    init_db(db_path)
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE jobs
            SET
              status = ?,
              application_notes = CASE
                WHEN ? IS NULL THEN application_notes
                ELSE ?
              END,
              applied_at = CASE
                WHEN ? = 'applied' THEN CURRENT_TIMESTAMP
                ELSE applied_at
              END,
              last_action_at = CURRENT_TIMESTAMP,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (status, note, note, status, job_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def execute_schema(db_path: Path = DEFAULT_DB_PATH) -> Iterable[str]:
    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' ORDER BY name;"
        ).fetchall()
    return [row["sql"] for row in rows if row["sql"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize local SQLite storage.")
    parser.add_argument("--init", action="store_true", help="Create local tables.")
    parser.add_argument(
        "--count",
        choices=["jobs", "generated_documents"],
        help="Print the row count for a local table.",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="Print recently saved jobs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Limit for --list-jobs.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path.",
    )
    args = parser.parse_args(argv)

    if args.init:
        init_db(args.db_path)
        print(f"Initialized database at {args.db_path}")
        return 0

    if args.count:
        init_db(args.db_path)
        print(count_rows(args.count, args.db_path))
        return 0

    if args.list_jobs:
        rows = list_jobs(args.db_path, limit=args.limit)
        if not rows:
            print("No jobs found.")
            return 0
        print(
            "id | workday_id | title | location | posting_date | fit | label | "
            "family | resume_type | resume_name | resume_path | status"
        )
        for row in rows:
            print(
                " | ".join(
                    [
                        str(row["id"]),
                        row["workday_id"] or "",
                        row["title"] or "",
                        row["location"] or "",
                        row["posting_date"] or "",
                        str(row["fit_score"] or ""),
                        row["fit_label"] or "",
                        row["job_family"] or "",
                        row["recommended_resume_type"] or "",
                        row["recommended_resume_name"] or "",
                        row["recommended_resume_path"] or "",
                        row["status"] or "",
                    ]
                )
            )
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
