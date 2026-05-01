"""CLI flow for pasted job descriptions."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.matching.fit_scorer import score_fit
from src.scraping.job_detail_parser import parse_job_description
from src.storage.db import DEFAULT_DB_PATH, insert_generated_document, upsert_job
from src.storage.models import FitResult, GeneratedDocumentRecord, JobRecord, ParsedJob
from src.utils.file_utils import read_text, safe_filename, write_text
from src.utils.text_cleaner import normalize_whitespace


DEFAULT_REPORT_DIR = Path("outputs/reports")


@dataclass(frozen=True)
class ManualReportResult:
    job_id: int
    workday_id: str
    output_path: Path
    parsed_job: ParsedJob
    fit_result: FitResult


def build_manual_report(
    raw_description: str,
    output_path: Path | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> ManualReportResult:
    cleaned = normalize_whitespace(raw_description)
    if not cleaned:
        raise ValueError("Job description text is empty.")

    parsed_job = parse_job_description(cleaned)
    fit_result = score_fit(parsed_job, cleaned)
    workday_id = manual_workday_id(cleaned)
    title = parsed_job.title or f"Manual Job {workday_id[-8:]}"

    job = JobRecord(
        workday_id=workday_id,
        title=title,
        department=parsed_job.department,
        location=parsed_job.location,
        pay_rate=parsed_job.pay_rate,
        hours=parsed_job.hours,
        url=None,
        raw_description=cleaned,
        parsed_json=parsed_job.model_dump_json(indent=2),
        fit_score=fit_result.score,
        fit_label=fit_result.label,
        job_family=fit_result.job_family,
        recommended_resume_type=fit_result.recommended_resume_type,
        recommended_resume_name=fit_result.recommended_resume_name,
        recommended_resume_path=fit_result.recommended_resume_path,
        status="new",
    )
    job_id = upsert_job(job, db_path=db_path)

    if output_path is None:
        output_path = default_report_path(parsed_job, workday_id)

    report = render_markdown_report(
        workday_id=workday_id,
        job_id=job_id,
        parsed_job=parsed_job,
        fit_result=fit_result,
        raw_description=cleaned,
    )
    write_text(output_path, report)
    insert_generated_document(
        GeneratedDocumentRecord(
            job_id=job_id,
            document_type="report",
            file_path=str(output_path),
        ),
        db_path=db_path,
    )

    return ManualReportResult(
        job_id=job_id,
        workday_id=workday_id,
        output_path=output_path,
        parsed_job=parsed_job,
        fit_result=fit_result,
    )


def manual_workday_id(raw_description: str) -> str:
    digest = hashlib.sha256(normalize_whitespace(raw_description).encode("utf-8")).hexdigest()
    return f"manual-{digest[:16]}"


def default_report_path(parsed_job: ParsedJob, workday_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = safe_filename(parsed_job.title or workday_id)
    return DEFAULT_REPORT_DIR / f"{timestamp}_{slug}.md"


def render_markdown_report(
    workday_id: str,
    job_id: int,
    parsed_job: ParsedJob,
    fit_result: FitResult,
    raw_description: str,
) -> str:
    lines = [
        "# Manual Job Fit Report",
        "",
        f"- Local Job ID: {job_id}",
        f"- Workday ID: {workday_id}",
        f"- Title: {_value(parsed_job.title)}",
        f"- Department: {_value(parsed_job.department)}",
        f"- Location: {_value(parsed_job.location)}",
        f"- Pay Rate: {_value(parsed_job.pay_rate)}",
        f"- Hours: {_value(parsed_job.hours)}",
        "",
        "## Recommendation",
        "",
        f"- Fit Score: {fit_result.score}/100",
        f"- Fit Label: {fit_result.label}",
        f"- Job Family: {_value(fit_result.job_family)}",
        f"- Recommended Resume Type: {fit_result.recommended_resume_type}",
        f"- Recommended Resume: {_value(fit_result.recommended_resume_name)}",
        f"- Recommended Resume Path: {_value(fit_result.recommended_resume_path)}",
        "",
        "## Reasons",
        "",
        _markdown_list(fit_result.reasons),
        "",
        "## Gaps",
        "",
        _markdown_list(fit_result.gaps),
        "",
        "## Parsed Fields",
        "",
        "### Minimum Qualifications",
        "",
        _markdown_list(parsed_job.minimum_qualifications),
        "",
        "### Preferred Qualifications",
        "",
        _markdown_list(parsed_job.preferred_qualifications),
        "",
        "### Essential Duties",
        "",
        _markdown_list(parsed_job.essential_duties),
        "",
        "### Required Skills",
        "",
        _markdown_list(parsed_job.required_skills),
        "",
        "### Software Tools",
        "",
        _markdown_list(parsed_job.software_tools),
        "",
        "### Keywords",
        "",
        ", ".join(parsed_job.keywords) if parsed_job.keywords else "Not found.",
        "",
        "## Raw Job Description",
        "",
        "```text",
        raw_description,
        "```",
        "",
    ]
    return "\n".join(lines)


def _markdown_list(values: list[str]) -> str:
    if not values:
        return "Not found."
    return "\n".join(f"- {value}" for value in values)


def _value(value: str | None) -> str:
    return value if value else "Not found"


def _read_stdin_job_text() -> str:
    print("Paste the job description, then send EOF when finished.")
    print("Windows PowerShell: Ctrl+Z then Enter. macOS/Linux: Ctrl+D.")
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse, score, store, and report on a pasted job description."
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        help="Read job description text from this file instead of stdin.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown report output path. Defaults to outputs/reports/<timestamp>_<title>.md.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path.",
    )
    args = parser.parse_args(argv)

    raw_description = read_text(args.input_file) if args.input_file else _read_stdin_job_text()
    try:
        result = build_manual_report(
            raw_description=raw_description,
            output_path=args.output,
            db_path=args.db_path,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Saved report: {result.output_path}")
    print(
        "Recommendation: "
        f"{result.fit_result.label} ({result.fit_result.score}/100), "
        f"{result.fit_result.recommended_resume_name or result.fit_result.recommended_resume_type}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
