from pathlib import Path

from src.scraping.workday_scraper import (
    _extract_detail_text,
    _is_probable_job_title,
    _looks_like_job_detail_page_text,
    _looks_like_results_page_text,
    _parse_job_cards_from_page_text,
    _parse_result_cards_from_page_text,
    _return_to_results_page,
    _wait_for_job_detail_page,
    build_workday_job,
    extract_workday_id,
    infer_location_from_text,
    parse_job_card_text,
    scrape_workday_jobs,
    stable_workday_id,
    store_workday_job,
)
from src.storage.db import count_rows


class _FakeLocator:
    def __init__(self, page: "_FakeDetailPage", selector: str, index: int = 0):
        self.page = page
        self.selector = selector
        self.index = index

    def count(self) -> int:
        return len(self.page.selector_texts.get(self.selector, []))

    def nth(self, index: int) -> "_FakeLocator":
        return _FakeLocator(self.page, self.selector, index)

    def inner_text(self, timeout: int | None = None) -> str:
        values = self.page.selector_texts.get(self.selector, [])
        if self.index >= len(values):
            return ""
        return values[self.index]


class _FakeDetailPage:
    def __init__(self, states: list[dict[str, list[str]]]):
        self.states = states
        self.state_index = 0
        self.waits = 0

    @property
    def selector_texts(self) -> dict[str, list[str]]:
        return self.states[self.state_index]

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def wait_for_timeout(self, wait_ms: int) -> None:
        self.waits += 1
        if self.state_index < len(self.states) - 1:
            self.state_index += 1


class _SlowResultsPage:
    def __init__(self, waits_after_goto: int):
        self.waits_after_goto = waits_after_goto
        self.goto_calls = 0
        self.go_back_calls = 0
        self.waits_since_goto = 0

    @property
    def selector_texts(self) -> dict[str, list[str]]:
        if self.goto_calls and self.waits_since_goto >= self.waits_after_goto:
            return {
                "body": [
                    """
                    Find Student Jobs
                    178 Results
                    Program Aide
                    JR119636 | Campus: Tempe | Posting Date: 05/18/2026
                    """
                ]
            }
        return {"body": ["Skip to main content\nAccessibility Overview"]}

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def wait_for_timeout(self, wait_ms: int) -> None:
        if self.goto_calls:
            self.waits_since_goto += 1

    def go_back(self, wait_until: str, timeout: int) -> None:
        self.go_back_calls += 1

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        self.goto_calls += 1
        self.waits_since_goto = 0


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
    assert _is_probable_job_title("Skip to main content") is False
    assert _is_probable_job_title("Skip To Results") is False
    assert _is_probable_job_title("Home") is False


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


def test_results_page_text_requires_results_count_and_cards() -> None:
    assert _looks_like_results_page_text(
        """
        Find Student Jobs
        178 Results
        Program Aide
        JR119636 | Campus: Tempe | Posting Date: 05/18/2026
        Robotic Design Lab Assistant
        JR121330 | Campus: Tempe | Posting Date: 05/18/2026
        """
    )

    assert not _looks_like_results_page_text(
        """
        Skip to main content
        Accessibility Overview
        """
    )


def test_detail_page_text_overrides_similar_job_rows() -> None:
    detail_text = """
    View Job Posting Details
    Undergraduate Peer Mentor
    Apply
    Job Details
    Job Requisition ID
    JR121065
    Similar Jobs
    Academic Services Aide
    JR120916 | Campus: Tempe | Posting Date: 05/18/2026
    """

    assert _looks_like_job_detail_page_text(detail_text)
    assert not _looks_like_results_page_text(detail_text)
    assert _parse_result_cards_from_page_text(detail_text) == []


def test_result_page_parser_keeps_only_real_search_results() -> None:
    cards = _parse_result_cards_from_page_text(
        """
        Find Student Jobs
        178 Results
        Program Aide
        JR119636 | Campus: Tempe | Posting Date: 05/18/2026
        Robotic Design Lab Assistant
        JR121330 | Campus: Tempe | Posting Date: 05/18/2026
        """
    )

    assert [card.workday_id for card in cards] == ["JR119636", "JR121330"]


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


def test_extract_detail_text_rejects_loading_only_workday_shell() -> None:
    page = _FakeDetailPage(
        [
            {
                "body": [
                    """
                    Skip to main content
                    Accessibility Overview
                    18
                    Home
                    Personal Resources
                    Saved
                    View Job Posting Details
                    Applicant Services Frontline Representative
                    Loading
                    View Job Posting Details - Workday page is loaded
                    """
                ]
            }
        ]
    )

    assert _extract_detail_text(page) == ""


def test_wait_for_job_detail_page_waits_until_description_is_loaded() -> None:
    loading_shell = {
        "body": [
            """
            Skip to main content
            Accessibility Overview
            View Job Posting Details
            Applicant Services Frontline Representative
            Loading
            View Job Posting Details - Workday page is loaded
            """
        ]
    }
    loaded_detail = {
        "body": [
            """
            Skip to main content
            Accessibility Overview
            View Job Posting Details
            Applicant Services Frontline Representative
            Applicant Services Frontline Representative
            Apply

            Job Profile:
            Student Worker III

            Job Family:
            Student Employee

            Job Description:
            Supports applicant services by answering questions, routing requests,
            and maintaining accurate records.

            Job Requisition ID:
            JR121562
            """
        ]
    }
    page = _FakeDetailPage([loading_shell, loaded_detail])

    assert _wait_for_job_detail_page(page, wait_ms=1, attempts=3)
    assert page.waits == 1


def test_return_to_results_page_waits_through_slow_workday_blank_reload() -> None:
    page = _SlowResultsPage(waits_after_goto=12)

    assert _return_to_results_page(page, "https://www.myworkday.com/asu/jobs", wait_ms=1)
    assert page.goto_calls == 1


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
