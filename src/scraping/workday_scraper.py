"""Bulk ASU Workday job extraction using a saved Playwright session."""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.auth.login_capture import DEFAULT_AUTH_STATE_PATH, DEFAULT_WORKDAY_URL
from src.auth.session_check import auth_state_exists, evaluate_session_page
from src.matching.fit_scorer import score_fit
from src.scraping.job_detail_parser import parse_job_description
from src.storage.db import DEFAULT_DB_PATH, upsert_job
from src.storage.models import JobRecord
from src.utils.text_cleaner import normalize_whitespace


JOB_CARD_SELECTORS = [
    '[data-automation-id="jobSearchResult"]',
    'li:has([data-automation-id="jobTitle"])',
    'div[role="listitem"]:has([data-automation-id="jobTitle"])',
    'article:has([data-automation-id="jobTitle"])',
    '[data-automation-id="jobTitle"]',
]

PROMPT_OPTION_SELECTOR = '[data-automation-id="promptOption"][role="link"], [data-automation-id="promptOption"]'

JOB_TITLE_SELECTORS = [
    '[data-automation-id="jobTitle"]',
    '[data-automation-id="promptOption"][role="link"]',
    '[data-automation-id="promptOption"]',
    'a[href*="/job/"]',
    '[role="link"]',
]

JOB_DETAIL_SELECTORS = [
    '[data-automation-id="jobPostingDescription"]',
    '[data-automation-id="jobPostingPage"]',
    '[data-automation-id="jobDetails"]',
    '[data-automation-id="jobPostingHeader"]',
    "main",
    "body",
]

JOB_ID_PATTERNS = [
    r"\b(?:job id|job req id|requisition id|req id)\s*[:#\-]\s*((?:JR|R)\d{4,}|REQ[0-9A-Za-z_\-]+)",
    r"\b(R\d{4,}|JR\d{4,}|REQ[0-9A-Za-z_\-]+)\b",
]


class SessionExpiredError(RuntimeError):
    """Raised when the saved Workday session no longer opens the jobs page."""


@dataclass(frozen=True)
class WorkdayJob:
    workday_id: str
    title: str
    department: str | None
    location: str | None
    posting_date: str | None
    url: str | None
    raw_description: str


@dataclass(frozen=True)
class JobCard:
    title: str
    workday_id: str | None
    location: str | None
    posting_date: str | None
    raw_text: str


@dataclass(frozen=True)
class ClickableJobCard:
    element: object
    card: JobCard


@dataclass(frozen=True)
class ScrapeSummary:
    jobs_seen: int
    jobs_saved: int
    job_ids: list[int]


def extract_workday_id(text: str, url: str | None = None) -> str | None:
    combined = f"{url or ''}\n{text}"
    for pattern in JOB_ID_PATTERNS:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            workday_id = _normalize_workday_id(match.group(1))
            if workday_id:
                return workday_id
    return None


def stable_workday_id(title: str, raw_description: str, url: str | None = None) -> str:
    explicit_id = extract_workday_id(raw_description, url)
    if explicit_id:
        return explicit_id

    digest_source = "\n".join([title.strip(), normalize_whitespace(raw_description), url or ""])
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    return f"workday-{digest[:16]}"


def infer_location_from_text(text: str) -> str | None:
    location_patterns = [
        r"\bTempe(?:\s+campus)?\b",
        r"\bDowntown Phoenix(?:\s+campus)?\b",
        r"\bPolytechnic(?:\s+campus)?\b",
        r"\bWest(?:\s+Valley)?(?:\s+campus)?\b",
        r"\bThunderbird(?:\s+campus)?\b",
        r"\bRemote\b",
        r"\bHybrid\b",
    ]
    for pattern in location_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def parse_job_card_text(card_text: str) -> JobCard:
    """Parse the result-list card text shown before opening a Workday job."""

    normalized = normalize_whitespace(card_text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    workday_id = extract_workday_id(normalized)
    location = _extract_card_location(normalized)
    posting_date = _extract_card_posting_date(normalized)
    title = _extract_card_title(lines, workday_id)

    return JobCard(
        title=title,
        workday_id=workday_id,
        location=location,
        posting_date=posting_date,
        raw_text=normalized,
    )


def build_workday_job(
    card_title: str,
    detail_text: str,
    url: str | None = None,
    card_text: str | None = None,
) -> WorkdayJob:
    raw_description = normalize_whitespace(detail_text)
    parsed = parse_job_description(raw_description)
    card = parse_job_card_text(card_text or card_title)
    parsed_title = parsed.title if parsed.title and not _looks_like_identifier_line(parsed.title) else None
    title = card.title or parsed_title or normalize_whitespace(card_title) or "Untitled Workday Job"
    location = card.location or parsed.location or infer_location_from_text(raw_description)
    workday_id = card.workday_id or stable_workday_id(title, raw_description, url)
    posting_date = card.posting_date or _extract_card_posting_date(raw_description)

    return WorkdayJob(
        workday_id=workday_id,
        title=title,
        department=parsed.department,
        location=location,
        posting_date=posting_date,
        url=url,
        raw_description=raw_description,
    )


def store_workday_job(job: WorkdayJob, db_path: Path = DEFAULT_DB_PATH) -> int:
    parsed = parse_job_description(job.raw_description)
    parsed = parsed.model_copy(
        update={
            "title": job.title,
            "location": job.location or parsed.location,
        }
    )

    fit = score_fit(parsed, job.raw_description)
    record = JobRecord(
        workday_id=job.workday_id,
        title=job.title,
        department=parsed.department or job.department,
        location=job.location or parsed.location,
        pay_rate=parsed.pay_rate,
        hours=parsed.hours,
        posting_date=job.posting_date,
        url=job.url,
        raw_description=job.raw_description,
        parsed_json=parsed.model_dump_json(indent=2),
        fit_score=fit.score,
        fit_label=fit.label,
        job_family=fit.job_family,
        recommended_resume_type=fit.recommended_resume_type,
        recommended_resume_name=fit.recommended_resume_name,
        recommended_resume_path=fit.recommended_resume_path,
        status="new",
    )
    return upsert_job(record, db_path=db_path)


def scrape_workday_jobs(
    workday_url: str = DEFAULT_WORKDAY_URL,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int | None = None,
    headless: bool = True,
    wait_ms: int = 750,
    max_scrolls: int = 50,
    idle_rounds: int = 3,
    click_timeout_ms: int = 5_000,
    debug_dump_dir: Path | None = None,
) -> ScrapeSummary:
    if not auth_state_exists(auth_state_path):
        raise FileNotFoundError(
            f"Missing auth state at {auth_state_path}. Run `python -m src.auth.login_capture` first."
        )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `playwright install` first."
        ) from exc

    saved_local_ids: list[int] = []
    seen_workday_ids: set[str] = set()
    processed_card_keys: set[str] = set()
    jobs_seen = 0

    print(f"Opening Workday jobs page: {workday_url}", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(auth_state_path))
        page = context.new_page()

        try:
            page.goto(workday_url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass

            page_text = _safe_body_text(page)
            if not evaluate_session_page(page.url, page_text):
                raise SessionExpiredError(
                    "Saved Workday session appears expired. Run `python -m src.auth.login_capture` again."
                )
            print("Saved session is valid. Starting job extraction.", flush=True)
            _reset_results_scroll_to_top(page, wait_ms)

            scrolls_without_new_jobs = 0
            previous_seen_count = 0

            for scroll_number in range(1, max_scrolls + 1):
                cards = _collect_ordered_result_cards(page)
                visible_count = len(cards)
                print(
                    f"Scan {scroll_number}/{max_scrolls}: found {visible_count} visible candidate card(s).",
                    flush=True,
                )
                if cards:
                    print(
                        f"First candidate on this scan: {cards[0].title} ({cards[0].workday_id or 'no id'})",
                        flush=True,
                    )
                    print(
                        "Candidate preview: "
                        + " | ".join(
                            f"{card.title} ({card.workday_id or 'no id'})"
                            for card in cards[:5]
                        ),
                        flush=True,
                    )
                if visible_count == 0:
                    _write_debug_dump(page, debug_dump_dir, f"scan_{scroll_number}_no_candidates")

                for card_index, card in enumerate(cards):
                    if limit is not None and jobs_seen >= limit:
                        break

                    card_text = card.raw_text
                    if not card.title:
                        continue
                    card_key = _card_key(card_text)
                    if card_key in processed_card_keys:
                        continue

                    try:
                        clicked = _click_ordered_result_card(page, card, cards, card_index, click_timeout_ms)
                        if not clicked:
                            continue
                    except Exception:
                        continue

                    page.wait_for_timeout(wait_ms)
                    detail_text = _extract_detail_text(page)
                    if not detail_text:
                        continue

                    workday_job = build_workday_job(
                        card_title=card.title,
                        detail_text=detail_text,
                        url=page.url,
                        card_text=card_text,
                    )
                    if workday_job.workday_id in seen_workday_ids:
                        processed_card_keys.add(card_key)
                        _return_to_results_page(page, workday_url, wait_ms)
                        continue

                    seen_workday_ids.add(workday_job.workday_id)
                    processed_card_keys.add(card_key)
                    jobs_seen += 1
                    local_id = store_workday_job(workday_job, db_path=db_path)
                    saved_local_ids.append(local_id)
                    print(
                        f"Saved {jobs_seen}{_limit_suffix(limit)}: {workday_job.title} "
                        f"({workday_job.workday_id})"
                        f"{_posting_date_suffix(workday_job.posting_date)}",
                        flush=True,
                    )
                    _return_to_results_page(page, workday_url, wait_ms)

                if limit is not None and jobs_seen >= limit:
                    print(f"Reached scrape limit of {limit}.", flush=True)
                    break

                if jobs_seen == previous_seen_count:
                    scrolls_without_new_jobs += 1
                    print(
                        f"No new jobs found on this scan "
                        f"({scrolls_without_new_jobs}/{idle_rounds} idle scans).",
                        flush=True,
                    )
                else:
                    scrolls_without_new_jobs = 0
                    previous_seen_count = jobs_seen

                if scrolls_without_new_jobs >= idle_rounds:
                    print("Stopping because no new jobs appeared after repeated scans.", flush=True)
                    break

                page.mouse.wheel(0, 2_000)
                page.wait_for_timeout(wait_ms)
        finally:
            browser.close()

    return ScrapeSummary(
        jobs_seen=jobs_seen,
        jobs_saved=len(saved_local_ids),
        job_ids=saved_local_ids,
    )


def _collect_ordered_result_cards(page) -> list[JobCard]:
    cards = _parse_job_cards_from_page_text(_safe_body_text(page))
    if cards:
        return cards

    candidates = _collect_prompt_option_title_candidates(page)
    if candidates:
        return [candidate.card for candidate in candidates]

    return [candidate.card for candidate in _collect_job_card_candidates(page)]


def _click_ordered_result_card(
    page,
    card: JobCard,
    cards: list[JobCard],
    card_index: int,
    timeout_ms: int,
) -> bool:
    occurrence_index = _title_occurrence_index(cards, card_index)
    prompt_options = _matching_prompt_options(page, card.title)

    for handle in prompt_options:
        row_text = _nearest_job_row_text(handle)
        if card.workday_id and card.workday_id in row_text:
            _click_job_card(handle, timeout_ms)
            return True

    if occurrence_index < len(prompt_options):
        _click_job_card(prompt_options[occurrence_index], timeout_ms)
        return True

    if prompt_options:
        _click_job_card(prompt_options[0], timeout_ms)
        return True

    return False


def _matching_prompt_options(page, title: str) -> list[object]:
    matches: list[object] = []
    for handle in _visible_prompt_option_handles(page):
        if _prompt_option_title(handle).lower() == title.lower():
            matches.append(handle)

    return matches


def _title_occurrence_index(cards: list[JobCard], card_index: int) -> int:
    title = cards[card_index].title.lower()
    return sum(1 for card in cards[:card_index] if card.title.lower() == title)


def _return_to_results_page(page, workday_url: str, wait_ms: int) -> None:
    if _has_results_list(page):
        return

    try:
        page.go_back(wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(wait_ms)
    except Exception:
        pass

    if _has_results_list(page):
        print("Returned to results page.", flush=True)
        return

    try:
        page.goto(workday_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(wait_ms)
    except Exception:
        pass

    if _has_results_list(page):
        print("Reloaded results page.", flush=True)


def _reset_results_scroll_to_top(page, wait_ms: int) -> None:
    """Workday can restore nested-list scroll state from the saved session."""

    for _ in range(4):
        try:
            page.keyboard.press("Home")
        except Exception:
            pass

        try:
            page.mouse.wheel(0, -8_000)
        except Exception:
            pass

        try:
            page.evaluate(
                """
                () => {
                  window.scrollTo(0, 0);
                  document.documentElement.scrollTop = 0;
                  document.body.scrollTop = 0;

                  for (const el of Array.from(document.querySelectorAll('*'))) {
                    const style = window.getComputedStyle(el);
                    const canScrollY =
                      el.scrollHeight > el.clientHeight + 5 &&
                      !['hidden', 'clip'].includes(style.overflowY);
                    if (canScrollY) {
                      el.scrollTop = 0;
                    }
                  }
                }
                """
            )
        except Exception:
            pass

        page.wait_for_timeout(wait_ms)


def _has_results_list(page) -> bool:
    cards = _parse_job_cards_from_page_text(_safe_body_text(page))
    return len(cards) >= 2


def _collect_job_card_candidates(page) -> list[ClickableJobCard]:
    candidates = _collect_prompt_option_title_candidates(page)
    if candidates:
        return candidates

    candidates = _collect_structured_job_card_candidates(page)
    if candidates:
        return candidates
    return _collect_dom_text_job_card_candidates(page)


def _collect_prompt_option_title_candidates(page) -> list[ClickableJobCard]:
    candidates: list[ClickableJobCard] = []
    seen_keys: set[str] = set()

    handles = _visible_prompt_option_handles(page)
    page_text_cards = _parse_job_cards_from_page_text(_safe_body_text(page))
    used_page_text_indexes: set[int] = set()

    for index, handle in enumerate(handles):
        title = _prompt_option_title(handle)
        if not _is_probable_job_title(title):
            continue

        row_text = _nearest_job_row_text(handle)
        if row_text:
            card = parse_job_card_text(row_text)
            if not _is_valid_card(card):
                card = None
            else:
                card = JobCard(
                    title=title,
                    workday_id=card.workday_id,
                    location=card.location,
                    posting_date=card.posting_date,
                    raw_text=row_text,
                )
        else:
            card = None

        if card is None:
            page_text_match = _take_page_text_card_for_title(title, page_text_cards, used_page_text_indexes)
            if page_text_match is None:
                continue
            card = JobCard(
                title=title,
                workday_id=page_text_match.workday_id,
                location=page_text_match.location,
                posting_date=page_text_match.posting_date,
                raw_text=page_text_match.raw_text,
            )

        element_id = _safe_attribute(handle, "id") or str(index)
        card = JobCard(
            title=card.title,
            workday_id=card.workday_id,
            location=card.location,
            posting_date=card.posting_date,
            raw_text=f"{card.raw_text}\nElement ID: {element_id}",
        )

        key = _card_key(card.raw_text)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(ClickableJobCard(element=handle, card=card))

    return candidates


def _visible_prompt_option_handles(page) -> list[object]:
    try:
        handles = page.query_selector_all(PROMPT_OPTION_SELECTOR)
    except Exception:
        return []

    visible_handles = [handle for handle in handles if _is_visible(handle)]
    return sorted(visible_handles, key=_element_sort_key)


def _element_sort_key(element) -> tuple[float, float]:
    try:
        box = element.bounding_box()
    except Exception:
        box = None
    if not box:
        return (1_000_000.0, 1_000_000.0)
    return (float(box.get("y", 1_000_000.0)), float(box.get("x", 1_000_000.0)))


def _take_page_text_card_for_title(
    title: str,
    cards: list[JobCard],
    used_indexes: set[int],
) -> JobCard | None:
    for index, card in enumerate(cards):
        if index in used_indexes:
            continue
        if card.title.lower() == title.lower():
            used_indexes.add(index)
            return card
    return None


def _parse_job_cards_from_page_text(page_text: str) -> list[JobCard]:
    lines = [line.strip() for line in normalize_whitespace(page_text).splitlines() if line.strip()]
    cards: list[JobCard] = []
    seen_ids: set[str] = set()

    for index, line in enumerate(lines):
        inline_card = _parse_inline_job_card_line(line)
        if inline_card and inline_card.workday_id not in seen_ids:
            seen_ids.add(inline_card.workday_id or "")
            cards.append(inline_card)
            continue

        if index >= len(lines) - 1:
            continue

        title = line.strip()
        metadata = lines[index + 1].strip()
        if not _is_probable_job_title(title):
            continue
        if not _looks_like_job_metadata_line(metadata):
            continue

        card = parse_job_card_text(f"{title}\n{metadata}")
        if _is_valid_card(card) and card.workday_id not in seen_ids:
            seen_ids.add(card.workday_id or "")
            cards.append(card)

    return cards


def _parse_inline_job_card_line(line: str) -> JobCard | None:
    match = re.match(
        r"^(?P<title>.+?)\s+(?P<meta>(?:JR|R)\d{4,}\b.*?Posting Date:\s*\d{1,2}/\d{1,2}/\d{4}.*)$",
        line.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    title = match.group("title").strip()
    metadata = match.group("meta").strip()
    if not _is_probable_job_title(title):
        return None

    card = parse_job_card_text(f"{title}\n{metadata}")
    if not _is_valid_card(card):
        return None
    return card


def _collect_structured_job_card_candidates(page) -> list[ClickableJobCard]:
    locator = _job_card_locator(page)
    candidates: list[ClickableJobCard] = []
    seen_keys: set[str] = set()
    try:
        count = locator.count()
    except Exception:
        return candidates

    for index in range(count):
        candidate_locator = locator.nth(index)
        card_text = _safe_element_text(candidate_locator)
        card = parse_job_card_text(card_text)
        if not _is_valid_card(card):
            continue
        key = _card_key(card.raw_text)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(ClickableJobCard(element=candidate_locator, card=card))

    return candidates


def _collect_dom_text_job_card_candidates(page) -> list[ClickableJobCard]:
    selectors = [
        "a",
        "button",
        '[role="link"]',
        '[data-automation-id="promptOption"]',
        "[tabindex]",
    ]
    candidates: list[ClickableJobCard] = []
    seen_keys: set[str] = set()

    for selector in selectors:
        try:
            handles = page.query_selector_all(selector)
        except Exception:
            handles = []

        for handle in handles:
            if not _is_visible(handle):
                continue

            own_text = _safe_element_text(handle)
            ancestor_text = _nearest_job_row_text(handle)
            card_text = ancestor_text or own_text
            card = parse_job_card_text(card_text)
            if not _is_valid_card(card):
                continue
            if not _element_text_matches_card(own_text, card):
                continue

            key = _card_key(card.raw_text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidates.append(ClickableJobCard(element=handle, card=card))

    return candidates


def _job_card_locator(page):
    for selector in JOB_CARD_SELECTORS:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            count = 0
        if count > 0:
            return locator
    return page.locator(JOB_CARD_SELECTORS[-1])


def _click_job_card(element, timeout_ms: int) -> None:
    if hasattr(element, "locator"):
        for selector in JOB_TITLE_SELECTORS:
            title_link = element.locator(selector)
            try:
                if title_link.count() > 0 and title_link.first.is_visible(timeout=500):
                    title_link.first.click(timeout=timeout_ms)
                    return
            except Exception:
                continue

    element.scroll_into_view_if_needed(timeout=timeout_ms)
    element.click(timeout=timeout_ms)


def _extract_detail_text(page) -> str:
    candidates: list[str] = []
    for selector in JOB_DETAIL_SELECTORS:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), 3)
        except Exception:
            count = 0
        for index in range(count):
            text = _safe_locator_text(locator.nth(index))
            if text:
                candidates.append(text)
    if not candidates:
        return ""
    return normalize_whitespace(max(candidates, key=len))


def _safe_body_text(page) -> str:
    try:
        return normalize_whitespace(page.locator("body").inner_text(timeout=10_000))
    except Exception:
        return ""


def _safe_locator_text(locator) -> str:
    return _safe_element_text(locator)


def _safe_element_text(element) -> str:
    try:
        return normalize_whitespace(element.inner_text(timeout=5_000))
    except TypeError:
        try:
            return normalize_whitespace(element.inner_text())
        except Exception:
            pass
    except Exception:
        pass

    try:
        return normalize_whitespace(element.text_content(timeout=5_000) or "")
    except TypeError:
        try:
            return normalize_whitespace(element.text_content() or "")
        except Exception:
            return ""
    except Exception:
        return ""


def _safe_attribute(element, name: str) -> str | None:
    try:
        return element.get_attribute(name, timeout=1_000)
    except TypeError:
        try:
            return element.get_attribute(name)
        except Exception:
            return None
    except Exception:
        return None


def _prompt_option_title(element) -> str:
    for attribute in ["data-automation-label", "title", "aria-label"]:
        value = _safe_attribute(element, attribute)
        if value and normalize_whitespace(value):
            return normalize_whitespace(value)
    return _safe_element_text(element)


def _is_probable_job_title(value: str) -> bool:
    title = normalize_whitespace(value)
    if not title:
        return False
    if len(title) > 140:
        return False
    if re.fullmatch(r"\d+", title):
        return False
    if re.fullmatch(r"(?:JR|R)\d{4,}", title, flags=re.IGNORECASE):
        return False
    if title.lower() in {
        "job",
        "jobs",
        "find student jobs",
        "hybrid",
        "remote",
        "tempe",
        "west",
        "polytechnic",
        "downtown phoenix",
        "posting date",
    }:
        return False
    if re.search(
        r"\b(?:Campus:|Off-Campus:|Posting Date:|Pay Rate:|Hours:)",
        title,
        flags=re.IGNORECASE,
    ):
        return False
    return True


def _normalize_workday_id(value: str) -> str | None:
    cleaned = value.strip().strip(".,;:()[]{}")
    match = re.fullmatch(r"((?:JR|R)\d{4,}|REQ[0-9A-Za-z_\-]+)", cleaned, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper()


def _is_visible(element) -> bool:
    try:
        return bool(element.is_visible(timeout=750))
    except TypeError:
        try:
            return bool(element.is_visible())
        except Exception:
            return False
    except Exception:
        return False


def _nearest_job_row_text(handle) -> str:
    try:
        text = handle.evaluate(
            """
            (node) => {
              const hasMeta = (value) =>
                /\\b(?:JR|R)\\d{4,}\\b/i.test(value || "") &&
                /Posting Date:/i.test(value || "");
              let best = "";
              let el = node;
              for (let depth = 0; el && depth < 9; depth += 1, el = el.parentElement) {
                const text = (el.innerText || el.textContent || "").trim();
                if (!hasMeta(text)) {
                  continue;
                }
                const ids = text.match(/\\b(?:JR|R)\\d{4,}\\b/gi) || [];
                if (ids.length <= 2 && text.length <= 1000) {
                  return text;
                }
                if (!best) {
                  best = text;
                }
              }
              return best;
            }
            """
        )
    except Exception:
        return ""
    return normalize_whitespace(text or "")


def _is_valid_card(card: JobCard) -> bool:
    return bool(card.title and card.workday_id and card.posting_date)


def _element_text_matches_card(own_text: str, card: JobCard) -> bool:
    lowered = normalize_whitespace(own_text).lower()
    if not lowered:
        return True
    return card.title.lower() in lowered or (card.workday_id or "").lower() in lowered


def _looks_like_job_metadata_line(line: str) -> bool:
    return bool(
        re.search(r"\b(?:JR|R)\d{4,}\b", line, flags=re.IGNORECASE)
        and re.search(r"\bPosting Date:\s*\d{1,2}/\d{1,2}/\d{4}", line, flags=re.IGNORECASE)
    )


def _first_nonempty_line(text: str) -> str:
    for line in normalize_whitespace(text).splitlines():
        if line.strip():
            return line.strip()
    return ""


def _extract_card_title(lines: list[str], workday_id: str | None) -> str:
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if workday_id and workday_id in stripped:
            continue
        if re.search(r"\bCampus:|\bPosting Date:|\bHybrid\b", stripped, flags=re.IGNORECASE):
            continue
        if _looks_like_identifier_line(stripped):
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        return stripped
    return ""


def _extract_card_location(text: str) -> str | None:
    location_parts: list[str] = []

    off_campus_match = re.search(r"\bOff-Campus:\s*([^|\n]+)", text, flags=re.IGNORECASE)
    if off_campus_match:
        location_parts.append(f"Off-Campus: {off_campus_match.group(1).strip()}")
    else:
        campus_match = re.search(r"(?<!Off-)\bCampus:\s*([^|\n]+)", text, flags=re.IGNORECASE)
        if campus_match:
            campus = campus_match.group(1).strip()
            if campus.lower().endswith("campus"):
                location_parts.append(campus)
            else:
                location_parts.append(f"{campus} campus")

    for mode in ["Hybrid", "Remote"]:
        if re.search(rf"\b{mode}\b", text, flags=re.IGNORECASE):
            location_parts.append(mode)

    if location_parts:
        return "; ".join(location_parts)

    return infer_location_from_text(text)


def _extract_card_posting_date(text: str) -> str | None:
    match = re.search(r"Posting Date:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _card_key(card_text: str) -> str:
    normalized = normalize_whitespace(card_text).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]


def _limit_suffix(limit: int | None) -> str:
    return f"/{limit}" if limit is not None else ""


def _posting_date_suffix(posting_date: str | None) -> str:
    return f" | Posted {posting_date}" if posting_date else ""


def _write_debug_dump(page, debug_dump_dir: Path | None, reason: str) -> None:
    if debug_dump_dir is None:
        return

    debug_dump_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "_", reason).strip("_")
    base_path = debug_dump_dir / f"{timestamp}_{safe_reason}"

    try:
        body_text = _safe_body_text(page)
        (base_path.with_suffix(".txt")).write_text(body_text, encoding="utf-8")
    except Exception:
        pass

    try:
        page.screenshot(path=str(base_path.with_suffix(".png")), full_page=True)
    except Exception:
        pass

    print(f"Wrote debug dump to {base_path}.txt/.png", flush=True)


def _looks_like_identifier_line(text: str) -> bool:
    return bool(
        re.match(
            r"^(?:job id|job req id|requisition id|req id)\s*[:#\-]",
            text.strip(),
            flags=re.IGNORECASE,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bulk scrape ASU Workday student jobs using saved Playwright auth state."
    )
    parser.add_argument("--url", default=DEFAULT_WORKDAY_URL, help="ASU Workday student jobs URL.")
    parser.add_argument(
        "--auth-state-path",
        type=Path,
        default=DEFAULT_AUTH_STATE_PATH,
        help="Saved Playwright auth state path.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path.",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of jobs to scrape.")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while scraping.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=750,
        help="Gentle wait between Workday interactions in milliseconds.",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=50,
        help="Maximum listing scroll attempts.",
    )
    parser.add_argument(
        "--idle-rounds",
        type=int,
        default=3,
        help="Stop after this many scans find no new jobs.",
    )
    parser.add_argument(
        "--click-timeout-ms",
        type=int,
        default=5_000,
        help="Maximum wait for each job-card click.",
    )
    parser.add_argument(
        "--debug-dump-dir",
        type=Path,
        help="Write page text and screenshot dumps here when no job cards are found.",
    )
    args = parser.parse_args(argv)

    try:
        summary = scrape_workday_jobs(
            workday_url=args.url,
            auth_state_path=args.auth_state_path,
            db_path=args.db_path,
            limit=args.limit,
            headless=not args.headed,
            wait_ms=args.wait_ms,
            max_scrolls=args.max_scrolls,
            idle_rounds=args.idle_rounds,
            click_timeout_ms=args.click_timeout_ms,
            debug_dump_dir=args.debug_dump_dir,
        )
    except KeyboardInterrupt:
        print("Scrape interrupted by user. Jobs already saved in SQLite are kept.")
        return 130
    except (FileNotFoundError, SessionExpiredError, RuntimeError) as error:
        print(f"Error: {error}")
        return 1

    print(f"Saved {summary.jobs_saved} job(s) to {args.db_path}.")
    if summary.jobs_saved == 0:
        print("No jobs were saved. Try `--headed` to inspect the Workday page visually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
