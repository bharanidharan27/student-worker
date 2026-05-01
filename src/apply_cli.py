"""CLI-assisted apply queue for manual Workday applications."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import webbrowser
from pathlib import Path
from typing import Callable

from src.auth.login_capture import DEFAULT_AUTH_STATE_PATH
from src.apply_automation import ApplicationProfile, auto_apply_job, auto_apply_queue
from src.storage.db import (
    DEFAULT_DB_PATH,
    get_job_by_id,
    list_apply_queue,
    update_job_status,
)


BrowserOpener = Callable[[str], bool]


def render_queue(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "No actionable jobs found."

    lines = [
        "id | title | fit | label | family | posting_date | status | resume",
    ]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row["id"]),
                    row["title"] or "",
                    str(row["fit_score"] or ""),
                    row["fit_label"] or "",
                    row["job_family"] or "",
                    row["posting_date"] or "",
                    row["status"] or "",
                    row["recommended_resume_name"] or "",
                ]
            )
        )
    return "\n".join(lines)


def render_apply_packet(row: sqlite3.Row) -> str:
    resume_path = row["recommended_resume_path"] or "Not found"
    workday_url = row["url"] or "Not stored. Search Workday by the Workday ID."
    notes = row["application_notes"] or "None"

    return "\n".join(
        [
            "# Apply Packet",
            "",
            f"Local Job ID: {row['id']}",
            f"Workday ID: {row['workday_id'] or 'Not found'}",
            f"Title: {row['title'] or 'Not found'}",
            f"Location: {row['location'] or 'Not found'}",
            f"Posting Date: {row['posting_date'] or 'Not found'}",
            f"Fit: {row['fit_score'] or 'Not scored'}/100 ({row['fit_label'] or 'Not labeled'})",
            f"Job Family: {row['job_family'] or 'Not found'}",
            f"Status: {row['status'] or 'new'}",
            f"Recommended Resume: {row['recommended_resume_name'] or 'Not found'}",
            f"Resume Path: {resume_path}",
            f"Workday URL: {workday_url}",
            f"Notes: {notes}",
            "",
            "Checklist:",
            "1. Open the Workday job.",
            "2. Upload the listed resume PDF.",
            "3. Submit manually in Workday.",
            "4. Run `python -m src.apply_cli --mark-applied "
            f"{row['id']}` after submitting.",
        ]
    )


def open_job_url(
    job_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    opener: BrowserOpener = webbrowser.open,
) -> tuple[bool, str]:
    row = get_job_by_id(job_id, db_path=db_path)
    if row is None:
        return False, f"No job found with local id {job_id}."

    url = row["url"]
    if not url:
        return (
            True,
            f"No Workday URL stored for job {job_id}. Search Workday for {row['workday_id'] or row['title']}.",
        )

    opened = opener(url)
    if opened:
        return True, f"Opened Workday URL for job {job_id}: {url}"
    return False, f"Could not open Workday URL for job {job_id}: {url}"


def mark_status(
    job_id: int,
    status: str,
    note: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[bool, str]:
    updated = update_job_status(job_id=job_id, status=status, note=note, db_path=db_path)
    if not updated:
        return False, f"No job found with local id {job_id}."
    return True, f"Marked job {job_id} as {status}."


def next_job_id(db_path: Path = DEFAULT_DB_PATH, min_score: int = 0, fit_label: str = "") -> int | None:
    rows = list_apply_queue(db_path=db_path, limit=50)
    for row in rows:
        if (row["fit_score"] or 0) < min_score:
            continue
        if fit_label and row["fit_label"] != fit_label:
            continue
        return int(row["id"])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual apply queue for saved jobs.")
    parser.add_argument("--queue", action="store_true", help="Print ranked actionable jobs.")
    parser.add_argument("--next", action="store_true", help="Print the apply packet for the next best job.")
    parser.add_argument("--job-id", type=int, help="Print an apply packet for a saved job.")
    parser.add_argument("--open", type=int, metavar="JOB_ID", help="Open the stored Workday URL.")
    parser.add_argument(
        "--auto-apply",
        type=int,
        metavar="JOB_ID",
        help="Use Playwright to open Workday and upload the recommended resume.",
    )
    parser.add_argument(
        "--auto-apply-next",
        action="store_true",
        help="Auto-apply the next best queued job. Defaults to Strong Fit with score >= 80.",
    )
    parser.add_argument(
        "--auto-apply-queue",
        action="store_true",
        help="Auto-apply filtered queue jobs. Defaults to Strong Fit with score >= 80.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Allow auto-apply to click final Submit when no required-field blockers are detected.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser during auto-apply.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=80,
        help="Minimum fit score for --auto-apply-queue.",
    )
    parser.add_argument(
        "--fit-label",
        default="Strong Fit",
        help="Fit label filter for --auto-apply-queue. Use an empty string to disable.",
    )
    parser.add_argument("--mark-reviewing", type=int, metavar="JOB_ID", help="Mark a job as reviewing.")
    parser.add_argument("--mark-applied", type=int, metavar="JOB_ID", help="Mark a job as applied.")
    parser.add_argument("--mark-skipped", type=int, metavar="JOB_ID", help="Mark a job as skipped.")
    parser.add_argument("--note", help="Optional note for a status update.")
    parser.add_argument("--limit", type=int, default=10, help="Limit for --queue.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument(
        "--auth-state-path",
        type=Path,
        default=DEFAULT_AUTH_STATE_PATH,
        help="Saved Workday Playwright auth state.",
    )
    parser.add_argument(
        "--debug-dump-dir",
        type=Path,
        help="Write auto-apply page text/screenshots when review is needed.",
    )
    parser.add_argument(
        "--click-timeout-ms",
        type=int,
        default=10_000,
        help="Element click/upload timeout for auto-apply.",
    )
    parser.add_argument(
        "--applicant-name",
        default="Bharanidharan Maheswaran",
        help="Name to fill in self-identification forms during auto-apply.",
    )
    args = parser.parse_args(argv)

    selected_actions = [
        args.queue,
        args.next,
        args.job_id is not None,
        args.open is not None,
        args.auto_apply is not None,
        args.auto_apply_next,
        args.auto_apply_queue,
        args.mark_reviewing is not None,
        args.mark_applied is not None,
        args.mark_skipped is not None,
    ]
    if sum(1 for selected in selected_actions if selected) != 1:
        parser.error("Choose exactly one action.")

    if args.queue:
        print(render_queue(list_apply_queue(db_path=args.db_path, limit=args.limit)))
        return 0

    if args.next:
        job_id = next_job_id(args.db_path)
        if job_id is None:
            print("No actionable jobs found.", file=sys.stderr)
            return 1
        row = get_job_by_id(job_id, db_path=args.db_path)
        print(render_apply_packet(row))
        return 0

    if args.job_id is not None:
        row = get_job_by_id(args.job_id, db_path=args.db_path)
        if row is None:
            print(f"No job found with local id {args.job_id}.", file=sys.stderr)
            return 1
        print(render_apply_packet(row))
        return 0

    if args.open is not None:
        ok, message = open_job_url(args.open, db_path=args.db_path)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.auto_apply is not None:
        application_profile = ApplicationProfile(applicant_name=args.applicant_name)
        result = auto_apply_job(
            args.auto_apply,
            db_path=args.db_path,
            auth_state_path=args.auth_state_path,
            submit=args.submit,
            headed=args.headed,
            debug_dump_dir=args.debug_dump_dir,
            timeout_ms=args.click_timeout_ms,
            application_profile=application_profile,
        )
        print(result.message, file=sys.stdout if result.ok else sys.stderr)
        return 0 if result.ok else 1

    if args.auto_apply_next:
        job_id = next_job_id(args.db_path, min_score=args.min_score, fit_label=args.fit_label)
        if job_id is None:
            print("No job matched the auto-apply-next filters.", file=sys.stderr)
            return 1
        application_profile = ApplicationProfile(applicant_name=args.applicant_name)
        result = auto_apply_job(
            job_id,
            db_path=args.db_path,
            auth_state_path=args.auth_state_path,
            submit=args.submit,
            headed=args.headed,
            debug_dump_dir=args.debug_dump_dir,
            timeout_ms=args.click_timeout_ms,
            application_profile=application_profile,
        )
        print(f"Job {job_id}: {result.message}", file=sys.stdout if result.ok else sys.stderr)
        return 0 if result.ok else 1

    if args.auto_apply_queue:
        application_profile = ApplicationProfile(applicant_name=args.applicant_name)
        results = auto_apply_queue(
            db_path=args.db_path,
            auth_state_path=args.auth_state_path,
            limit=args.limit,
            min_score=args.min_score,
            fit_label=args.fit_label,
            submit=args.submit,
            headed=args.headed,
            debug_dump_dir=args.debug_dump_dir,
            timeout_ms=args.click_timeout_ms,
            application_profile=application_profile,
        )
        if not results:
            print("No jobs matched the auto-apply queue filters.")
            return 0
        for result in results:
            status = "submitted" if result.submitted else "review" if result.needs_review else "failed"
            print(f"{result.job_id} | {status} | {result.message}")
        return 0 if all(result.ok or result.needs_review for result in results) else 1

    status_actions = [
        (args.mark_reviewing, "reviewing"),
        (args.mark_applied, "applied"),
        (args.mark_skipped, "skipped"),
    ]
    for job_id, status in status_actions:
        if job_id is not None:
            ok, message = mark_status(job_id, status, note=args.note, db_path=args.db_path)
            print(message, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
