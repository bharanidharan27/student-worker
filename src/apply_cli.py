"""CLI-assisted apply queue for manual Workday applications."""

from __future__ import annotations

import argparse
import re
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
PromptReader = Callable[[str], str]


def render_picker_menu(rows: list[sqlite3.Row]) -> str:
    """Render the interactive picker menu used by ``--pick``.

    Each row is shown with a 1-based menu number so the user never has to
    type or remember the underlying database id.
    """
    if not rows:
        return "No actionable jobs found. Run the scraper first."

    lines = ["Pick a job to auto-apply:", ""]
    for index, row in enumerate(rows, start=1):
        title = row["title"] or "Untitled job"
        fit = row["fit_score"]
        fit_label = row["fit_label"] or "-"
        location = row["location"] or "-"
        posted = row["posting_date"] or "-"
        status = row["status"] or "new"
        fit_text = f"{fit}/100 {fit_label}" if fit is not None else fit_label
        lines.append(f"  [{index}] {title}")
        lines.append(
            f"      fit: {fit_text} | location: {location} |"
            f" posted: {posted} | status: {status}"
        )
    lines.append("")
    lines.append("Type the number of the job to apply for, or 'q' to quit.")
    return "\n".join(lines)


def parse_picker_choice(answer: str, total: int) -> int | None:
    """Convert the user's free-text picker answer into a 1-based index.

    Returns ``None`` for any input that should not advance the picker, such
    as an empty answer, ``q``/``quit``/``exit``, or a number that is out of
    range. Whitespace and a leading ``#`` are stripped so ``#2`` and ``2``
    behave the same.
    """
    cleaned = answer.strip().lstrip("#").strip()
    if not cleaned:
        return None
    if cleaned.lower() in {"q", "quit", "exit"}:
        return None
    if not cleaned.isdigit():
        return None
    choice = int(cleaned)
    if choice < 1 or choice > total:
        return None
    return choice


def extract_workday_id_from_url(url: str) -> str | None:
    """Pull the Workday requisition id (e.g. ``JR12345``) out of a URL.

    Workday URLs use a few shapes; this captures the most common ones used
    by ASU. ``None`` is returned when no recognizable id is present.
    """
    if not url:
        return None
    patterns = [
        r"(JR[\-_]?\d+)",
        r"/job/[^/]+/(?:[^/]+_)?(R-?\d+)",
        r"_(R-?\d+)",
        r"/job/(\d{5,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper().replace("_", "-")
    return None


def find_job_id_by_url(
    url: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int | None, str]:
    """Match a pasted Workday URL to a saved local job id.

    Returns ``(job_id, message)``. ``job_id`` is ``None`` when no saved job
    matches; ``message`` always explains what happened so the caller can
    surface a friendly error to the user.
    """
    if not url or not url.strip():
        return None, "No URL was provided."

    cleaned_url = url.strip()
    workday_id = extract_workday_id_from_url(cleaned_url)

    rows = list_apply_queue(db_path=db_path, limit=500)
    # Direct URL match wins.
    for row in rows:
        if row["url"] and row["url"].strip() == cleaned_url:
            return int(row["id"]), f"Matched job {row['id']} by URL."

    # Fall back to matching the workday id parsed out of the URL.
    if workday_id:
        for row in rows:
            row_workday_id = (row["workday_id"] or "").upper().replace("_", "-")
            if row_workday_id == workday_id:
                return int(row["id"]), f"Matched job {row['id']} by Workday id {workday_id}."

    detail = f" (Workday id {workday_id})" if workday_id else ""
    return (
        None,
        f"No saved job matched that URL{detail}. Run the scraper first or"
        " use the picker.",
    )


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


def _run_auto_apply_for_job_id(args, job_id: int) -> int:
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


def run_picker(
    args,
    reader: PromptReader | None = None,
    writer: Callable[[str], None] = print,
) -> int:
    """Show a numbered menu and auto-apply for whatever the user picks.

    The picker hides the underlying database id entirely; the user only
    sees a list of `[1] Title` rows and types a single digit. Hitting
    enter without typing anything, or typing ``q``, exits cleanly.
    """
    rows = list_apply_queue(db_path=args.db_path, limit=args.limit)
    if not rows:
        writer("No actionable jobs found. Run the scraper first.")
        return 1

    writer(render_picker_menu(rows))
    # Resolve ``input`` lazily so test suites that monkeypatch
    # ``builtins.input`` (and any future stdin replacement) take effect.
    prompt_reader = reader if reader is not None else input
    try:
        answer = prompt_reader("Your pick: ")
    except EOFError:
        writer("\nNo selection. Exiting.")
        return 1

    choice = parse_picker_choice(answer, len(rows))
    if choice is None:
        writer("No valid selection. Exiting.")
        return 1

    selected = rows[choice - 1]
    job_id = int(selected["id"])
    title = selected["title"] or "Untitled job"
    writer(f"Applying for: {title}")
    return _run_auto_apply_for_job_id(args, job_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual apply queue for saved jobs.")
    parser.add_argument("--queue", action="store_true", help="Print ranked actionable jobs.")
    parser.add_argument("--next", action="store_true", help="Print the apply packet for the next best job.")
    parser.add_argument("--job-id", type=int, help="Print an apply packet for a saved job.")
    parser.add_argument("--open", type=int, metavar="JOB_ID", help="Open the stored Workday URL.")
    parser.add_argument(
        "--pick",
        action="store_true",
        help="Show a numbered list of jobs and auto-apply for the one you pick.",
    )
    parser.add_argument(
        "--auto-apply-url",
        metavar="URL",
        help="Auto-apply by pasting a Workday job URL instead of looking up an id.",
    )
    parser.add_argument(
        "--auto-apply",
        type=int,
        metavar="JOB_ID",
        help=argparse.SUPPRESS,  # advanced, hidden from the default --help output
    )
    parser.add_argument(
        "--auto-apply-next",
        action="store_true",
        help="Auto-apply the top-ranked Strong Fit job. The simplest one-shot option.",
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
        args.pick,
        args.auto_apply_url is not None,
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

    if args.pick:
        return run_picker(args)

    if args.auto_apply_url is not None:
        job_id, message = find_job_id_by_url(args.auto_apply_url, db_path=args.db_path)
        if job_id is None:
            print(message, file=sys.stderr)
            return 1
        print(message)
        return _run_auto_apply_for_job_id(args, job_id)

    if args.auto_apply is not None:
        return _run_auto_apply_for_job_id(args, args.auto_apply)

    if args.auto_apply_next:
        job_id = next_job_id(args.db_path, min_score=args.min_score, fit_label=args.fit_label)
        if job_id is None:
            print("No job matched the auto-apply-next filters.", file=sys.stderr)
            return 1
        return _run_auto_apply_for_job_id(args, job_id)

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
