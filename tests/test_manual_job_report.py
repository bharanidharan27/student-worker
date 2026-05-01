from pathlib import Path

from src.manual_job_report import build_manual_report
from src.storage.db import count_rows


def test_manual_report_writes_markdown_and_deduplicates_job(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    output_path = tmp_path / "report.md"
    raw = """
    Job Title: AI Product Assistant
    Department: EdPlus
    Location: Tempe campus
    Hours: 10-20 hours
    Responsibilities: Write user stories, support AI testing, document requirements,
    communicate with stakeholders, and analyze product feedback.
    Minimum Qualifications: Current ASU student.
    """

    first = build_manual_report(raw, output_path=output_path, db_path=db_path)
    second = build_manual_report(raw, output_path=output_path, db_path=db_path)

    report = output_path.read_text(encoding="utf-8")
    assert first.job_id == second.job_id
    assert count_rows("jobs", db_path) == 1
    assert "# Manual Job Fit Report" in report
    assert "AI Product Assistant" in report
    assert "Recommended Resume Type: product_ai" in report
    assert "Job Family: product_ai" in report
    assert "Recommended Resume: Bharanidharan_M_PartTime_Resume.pdf" in report
