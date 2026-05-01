from pathlib import Path

from src.scraping.workday_scraper import (
    _is_probable_job_title,
    _parse_job_cards_from_page_text,
    build_workday_job,
    extract_workday_id,
    infer_location_from_text,
    parse_job_card_text,
    scrape_workday_jobs,
    stable_workday_id,
    store_workday_job,
)
from src.storage.db import count_rows


def test_extract_workday_id_from_text_and_url() -> None:
    assert extract_workday_id("Job ID: R123456 Student Worker") == "R123456"
    assert extract_workday_id("", "https://example.com/job/JR987654") == "JR987654"
    assert extract_workday_id("Job Details Job Profile Worker") is None


def test_stable_workday_id_uses_explicit_id_or_hash() -> None:
    assert stable_workday_id("Title", "Requisition ID: R100000") == "R100000"
    assert stable_workday_id("Title", "Plain description").startswith("workday-")


def test_infer_location_from_text() -> None:
    assert infer_location_from_text("This role is on the Tempe campus.") == "Tempe campus"
    assert infer_location_from_text("Remote student support role.") == "Remote"
    assert infer_location_from_text("No known place here.") is None


def test_parse_job_card_text_matches_workday_result_row() -> None:
    card = parse_job_card_text(
        """
        AI Product Assistant
        JR120023 | Off-Campus: Scottsdale | Hybrid | Posting Date: 04/24/2026
        """
    )

    assert card.title == "AI Product Assistant"
    assert card.workday_id == "JR120023"
    assert card.location == "Off-Campus: Scottsdale; Hybrid"
    assert card.posting_date == "04/24/2026"


def test_prompt_option_title_filter_accepts_real_titles_and_rejects_metadata() -> None:
    assert _is_probable_job_title("AI Product Assistant") is True
    assert _is_probable_job_title("Research Aide - SUMMER '26") is True
    assert _is_probable_job_title("8") is False
    assert _is_probable_job_title("JR120023") is False
    assert _is_probable_job_title("Campus: Tempe") is False
    assert _is_probable_job_title("Posting Date: 04/24/2026") is False


def test_parse_job_cards_from_page_text_preserves_visible_order() -> None:
    cards = _parse_job_cards_from_page_text(
        """
        Find Student Jobs
        Advising Office Aide
        JR120138 | Campus: Tempe | Posting Date: 04/24/2026
        Student Success Aide - DPC
        JR120032 | Campus: Downtown Phoenix | Posting Date: 04/24/2026
        AI Product Assistant
        JR120023 | Off-Campus: Scottsdale | Hybrid | Posting Date: 04/24/2026
        """
    )

    assert [card.title for card in cards] == [
        "Advising Office Aide",
        "Student Success Aide - DPC",
        "AI Product Assistant",
    ]
    assert [card.workday_id for card in cards] == ["JR120138", "JR120032", "JR120023"]
    assert cards[1].location == "Downtown Phoenix campus"
    assert cards[2].location == "Off-Campus: Scottsdale; Hybrid"


def test_parse_job_cards_from_page_text_handles_inline_rows() -> None:
    cards = _parse_job_cards_from_page_text(
        """
        Advising Office Aide JR120138 | Campus: Tempe | Posting Date: 04/24/2026
        Student Success Aide - DPC JR120032 | Campus: Downtown Phoenix | Posting Date: 04/24/2026
        """
    )

    assert [card.title for card in cards] == ["Advising Office Aide", "Student Success Aide - DPC"]
    assert [card.workday_id for card in cards] == ["JR120138", "JR120032"]


def test_build_workday_job_normalizes_detail_text() -> None:
    job = build_workday_job(
        card_title="Software Developer Assistant",
        detail_text="""
        Job ID: R123456
        Department: Engineering
        Location: Tempe campus
        Responsibilities: Build Python tools and document APIs.
        """,
        url="https://www.myworkday.com/asu/job/R123456",
        card_text="""
        Software Developer Assistant
        R123456 | Campus: Tempe | Posting Date: 04/24/2026
        """,
    )

    assert job.workday_id == "R123456"
    assert job.title == "Software Developer Assistant"
    assert job.department == "Engineering"
    assert job.location == "Tempe campus"
    assert job.posting_date == "04/24/2026"
    assert "Python tools" in job.raw_description


def test_store_workday_job_deduplicates_by_workday_id(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    job = build_workday_job(
        card_title="Software Developer Assistant",
        detail_text="""
        Job ID: R222222
        Location: ASU Tempe campus
        Hours: 10-20 hours
        Minimum Qualifications: Current Computer Science graduate student.
        Responsibilities: Develop Python APIs, write SQL queries, automate backend workflows,
        document technical work, and communicate with the team.
        """,
        url="https://www.myworkday.com/asu/job/R222222",
    )

    first_id = store_workday_job(job, db_path=db_path)
    second_id = store_workday_job(job, db_path=db_path)

    assert first_id == second_id
    assert count_rows("jobs", db_path) == 1


def test_scrape_requires_existing_auth_state(tmp_path: Path) -> None:
    missing_auth = tmp_path / "playwright" / ".auth" / "asu_workday.json"

    try:
        scrape_workday_jobs(auth_state_path=missing_auth, db_path=tmp_path / "jobs.sqlite", limit=1)
    except FileNotFoundError as error:
        assert "login_capture" in str(error)
    else:
        raise AssertionError("Expected missing auth state to raise FileNotFoundError")
