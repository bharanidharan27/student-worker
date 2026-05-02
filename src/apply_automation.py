"""Workday auto-apply browser automation with explicit submit control."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from src.auth.login_capture import DEFAULT_AUTH_STATE_PATH
from src.auth.session_check import auth_state_exists, evaluate_session_page
from src.storage.db import DEFAULT_DB_PATH, get_job_by_id, list_apply_queue, update_job_status


@dataclass(frozen=True)
class AutoApplyJob:
    id: int
    workday_id: str
    title: str
    url: str
    resume_path: Path
    fit_score: int | None
    fit_label: str | None


@dataclass(frozen=True)
class AutoApplyResult:
    job_id: int
    ok: bool
    submitted: bool
    needs_review: bool
    message: str


@dataclass(frozen=True)
class ApplicationProfile:
    applicant_name: str = "Bharanidharan Maheswaran"
    work_authorization: str = "Yes"
    enrolled_at_asu: str = "Yes"
    federal_work_study: str = "No"
    age_18_or_older: str = "Yes"
    hispanic_or_latino: str = "No"
    disability_status: str = "No, I do not have a disability and have not had one in the past"
    disability_language: str = "English"

    def today_for_workday(self) -> str:
        return datetime.now().strftime("%m / %d / %Y")


ApplyDriver = Callable[
    [AutoApplyJob, bool, bool, Path | None, Path, int, ApplicationProfile],
    AutoApplyResult,
]


def auto_apply_job(
    job_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    submit: bool = False,
    headed: bool = True,
    debug_dump_dir: Path | None = None,
    timeout_ms: int = 10_000,
    application_profile: ApplicationProfile | None = None,
    driver: ApplyDriver | None = None,
) -> AutoApplyResult:
    row = get_job_by_id(job_id, db_path=db_path)
    if row is None:
        return AutoApplyResult(job_id, False, False, False, f"No job found with local id {job_id}.")

    job, validation_error = build_auto_apply_job(row)
    if validation_error:
        update_job_status(job_id, "reviewing", f"Auto apply blocked: {validation_error}", db_path)
        return AutoApplyResult(job_id, False, False, True, validation_error)

    if not auth_state_exists(auth_state_path):
        message = f"Missing or expired auth state at {auth_state_path}. Run `python -m src.auth.login_capture`."
        update_job_status(job_id, "reviewing", f"Auto apply blocked: {message}", db_path)
        return AutoApplyResult(job_id, False, False, True, message)

    apply_driver = driver or _run_playwright_apply
    profile = application_profile or ApplicationProfile()
    result = apply_driver(job, submit, headed, debug_dump_dir, auth_state_path, timeout_ms, profile)
    if result.submitted:
        update_job_status(job_id, "applied", result.message, db_path)
    elif result.needs_review:
        update_job_status(job_id, "reviewing", result.message, db_path)
    return result


def auto_apply_queue(
    db_path: Path = DEFAULT_DB_PATH,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    limit: int = 10,
    min_score: int = 80,
    fit_label: str = "Strong Fit",
    submit: bool = False,
    headed: bool = True,
    debug_dump_dir: Path | None = None,
    timeout_ms: int = 10_000,
    application_profile: ApplicationProfile | None = None,
    driver: ApplyDriver | None = None,
) -> list[AutoApplyResult]:
    rows = list_apply_queue(db_path=db_path, limit=limit)
    selected = [
        row
        for row in rows
        if (row["fit_score"] or 0) >= min_score
        and (not fit_label or row["fit_label"] == fit_label)
    ]
    return [
        auto_apply_job(
            int(row["id"]),
            db_path=db_path,
            auth_state_path=auth_state_path,
            submit=submit,
            headed=headed,
            debug_dump_dir=debug_dump_dir,
            timeout_ms=timeout_ms,
            application_profile=application_profile,
            driver=driver,
        )
        for row in selected
    ]


def build_auto_apply_job(row) -> tuple[AutoApplyJob | None, str | None]:
    url = row["url"]
    if not url:
        return None, "No Workday URL is stored for this job."

    resume_value = row["recommended_resume_path"]
    if not resume_value:
        return None, "No recommended resume path is stored for this job."

    resume_path = Path(resume_value)
    if not resume_path.is_absolute():
        resume_path = Path.cwd() / resume_path
    if not resume_path.exists():
        return None, f"Recommended resume file does not exist: {resume_path}"

    return (
        AutoApplyJob(
            id=int(row["id"]),
            workday_id=row["workday_id"] or "",
            title=row["title"] or "",
            url=url,
            resume_path=resume_path,
            fit_score=row["fit_score"],
            fit_label=row["fit_label"],
        ),
        None,
    )


def _run_playwright_apply(
    job: AutoApplyJob,
    submit: bool,
    headed: bool,
    debug_dump_dir: Path | None,
    auth_state_path: Path,
    timeout_ms: int,
    profile: ApplicationProfile,
) -> AutoApplyResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `playwright install` first."
        ) from exc

    keep_open_for_review = headed
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(storage_state=str(auth_state_path))
        page = context.new_page()
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass

            page_text = _safe_body_text(page)
            if not evaluate_session_page(page.url, page_text):
                return AutoApplyResult(
                    job.id,
                    False,
                    False,
                    True,
                    "Saved Workday session appears expired. Run `python -m src.auth.login_capture`.",
                )

            if _open_selected_job_from_results(page, job, timeout_ms):
                page.wait_for_timeout(1_000)
                page_text = _safe_body_text(page)

            applied_marker = _extract_applied_marker(page_text)
            if applied_marker:
                return AutoApplyResult(
                    job.id,
                    True,
                    True,
                    False,
                    f"Job was already applied in Workday: {applied_marker}.",
                )

            if not _click_apply(page, timeout_ms):
                if _open_selected_job_from_results(page, job, timeout_ms):
                    page.wait_for_timeout(1_000)
                    page_text = _safe_body_text(page)
                    applied_marker = _extract_applied_marker(page_text)
                    if applied_marker:
                        return AutoApplyResult(
                            job.id,
                            True,
                            True,
                            False,
                            f"Job was already applied in Workday: {applied_marker}.",
                        )
                    if _click_apply(page, timeout_ms):
                        page.wait_for_timeout(1_000)
                        result = _complete_application_flow(page, job, profile, submit, timeout_ms, debug_dump_dir)
                        if result.needs_review:
                            _write_debug_dump(page, debug_dump_dir, job.id, "needs_review")
                        return result

                applied_marker = _extract_applied_marker(_safe_body_text(page))
                if applied_marker:
                    return AutoApplyResult(
                        job.id,
                        True,
                        True,
                        False,
                        f"Job was already applied in Workday: {applied_marker}.",
                    )
                _write_debug_dump(page, debug_dump_dir, job.id, "apply_button_not_found")
                return AutoApplyResult(job.id, False, False, True, "Apply button was not found.")

            page.wait_for_timeout(1_000)
            result = _complete_application_flow(page, job, profile, submit, timeout_ms, debug_dump_dir)
            if result.needs_review:
                _write_debug_dump(page, debug_dump_dir, job.id, "needs_review")
            return result
        finally:
            if not keep_open_for_review:
                browser.close()


@dataclass(frozen=True)
class SubmitResult:
    submitted: bool
    message: str


@dataclass(frozen=True)
class SectionResult:
    ok: bool
    message: str | None = None


# Sections where the user must manually verify or complete fields before the
# automation advances. The tool fills what it can, then waits for the user to
# click Save and Continue / Next themselves.
#
# My Experience is intentionally NOT in this list. Workday's resume parser
# pre-fills the section from the Quick Apply upload, and per the user's
# instruction the tool must do nothing on this page except click Next.
MANUAL_ADVANCE_SECTIONS: tuple[str, ...] = ()

# How long to wait for the user to manually advance past a manual section before
# giving up (in milliseconds). Default: 15 minutes.
MANUAL_ADVANCE_TIMEOUT_MS = 15 * 60 * 1_000
MANUAL_ADVANCE_POLL_MS = 1_000


_SECTION_LABELS: tuple[str, ...] = (
    "quick apply",
    "my experience",
    "application questions",
    "voluntary disclosures",
    "voluntary personal information",
    "self identify",
    "self-identification of disability",
    "review",
)


def _current_section_label(body_text_or_page) -> str | None:
    """Return the current Workday section.

    Detection order (first reliable signal wins):

    1. Feature detection on the live page — e.g. an empty file input
       means Quick Apply, a 'Hispanic or Latino' radio means Voluntary
       Disclosures, a signature checkbox means Review. This works
       regardless of how Workday labels the page.
    2. The visible heading text (when one matches a known section).
    3. Body-text substring match, but only when exactly one known
       label is present (otherwise the progress bar would mislead us).

    Test/legacy callers pass a string and only get path 3.
    """
    if isinstance(body_text_or_page, str):
        return _section_from_text(body_text_or_page)

    page = body_text_or_page

    # 1. Feature detection — most reliable, doesn't depend on labels.
    feature_label = _detect_section_by_features(page)
    if feature_label is not None:
        return feature_label

    # 2. Heading text.
    heading = _read_active_section_heading(page)
    if heading is not None:
        match = _section_from_text(heading)
        if match is not None:
            return match

    # 3. Body-text fallback.
    try:
        return _section_from_text(_safe_body_text(page))
    except Exception:
        return None


def _detect_section_by_features(page) -> str | None:
    """Detect the current Workday step by what's actually on the page.

    Each step has unique structural fingerprints we can recognise without
    depending on labels (which Workday tenants and themes vary):

    * Quick Apply: a file input is present and no resume is attached yet.
    * Review: a signature / 'I acknowledge' checkbox or 'Submit' button.
    * Self Identify: a CC-305 disclosure block (Workday OFCCP form).
    * Voluntary Disclosures: 'Hispanic or Latino' question on the page.
    * My Experience: 'Work Experience' + 'Education' headings/sections.
    * Application Questions: 'Are you eligible to work' or 'work-study'.
    """
    # Quick Apply: file input present.
    try:
        if page.locator("input[type='file']").count() > 0:
            # Even on a page that lists 'My Experience' content, Workday's
            # actual file input only renders on the Quick Apply step in
            # this flow. The body-text fallback then disambiguates.
            try:
                body_lower = _safe_body_text(page).lower()
            except Exception:
                body_lower = ""
            if (
                "work experience" not in body_lower
                and "education" not in body_lower
            ):
                return "quick apply"
    except Exception:
        pass

    # Body-text driven feature checks for later steps. Multi-label sidebar
    # text doesn't bother us here because we're keying on *content*.
    try:
        body_lower = _safe_body_text(page).lower()
    except Exception:
        return None

    if "i acknowledge" in body_lower or "electronic signature" in body_lower:
        return "review"
    if "cc-305" in body_lower or "section 503" in body_lower:
        return "self-identification of disability"
    if "hispanic or latino" in body_lower:
        return "voluntary disclosures"
    if "work experience" in body_lower and "education" in body_lower:
        return "my experience"
    if (
        "eligible to work in the united states" in body_lower
        or "federal work-study" in body_lower
        or "federal work study" in body_lower
    ):
        return "application questions"
    return None


def _section_from_text(text: str) -> str | None:
    """Best-effort section detection from a flat text blob.

    Used only when the heading lookup returns nothing. If the text
    contains multiple Workday section labels we can't tell which one
    is current (this happens with the progress bar pill or any sidebar
    fragment), so we return None and let the caller decide.
    """
    lowered = text.lower()
    matches = [label for label in _SECTION_LABELS if label in lowered]
    if len(matches) == 1:
        return matches[0]
    return None


def _read_active_section_heading(page) -> str | None:
    """Return the visible page heading text, ignoring nav and progress legends.

    Workday's ASU tenant renders the active section title centered above
    a progress bar pill. The pill's label list (e.g. 'Quick Apply'
    + 'My Experience' next-step pill) can also live inside heading-ish
    elements, so we accept a heading only when it contains *exactly one*
    known section label. Multiple labels in one element means we hit the
    progress legend or sidebar, not the actual page title.
    """
    candidates: list[str] = []
    try:
        for selector in (
            "main h1",
            "main h2",
            "[role='main'] h1",
            "[role='main'] h2",
            "h1[data-automation-id]",
            "h2[data-automation-id]",
            "h1",
            "h2",
        ):
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 6)
            except Exception:
                continue
            for index in range(count):
                try:
                    text = (loc.nth(index).inner_text(timeout=500) or "").strip()
                except Exception:
                    continue
                if text:
                    candidates.append(text)
    except Exception:
        return None

    # Prefer a heading that names exactly one Workday section. That filters
    # out the progress-bar legend which lists multiple section names at once.
    for text in candidates:
        labels_in_text = [lbl for lbl in _SECTION_LABELS if lbl in text.lower()]
        if len(labels_in_text) == 1:
            return text

    # Fallback: return the first non-empty heading even if it doesn't match
    # a known label — some pages (e.g. the 'My Information' step) have a
    # heading that doesn't appear in our enum but is still informative.
    for text in candidates:
        if text:
            return text
    return None


def _wait_for_user_to_advance(
    page,
    current_section: str,
    timeout_ms: int = MANUAL_ADVANCE_TIMEOUT_MS,
    poll_ms: int = MANUAL_ADVANCE_POLL_MS,
) -> bool:
    """Poll until the active Workday section changes away from ``current_section``.

    Uses the page heading (not body text) to detect the change so the
    sidebar that lists every label on every page can't fool us.
    Returns True when the section advanced, False when the timeout elapsed.
    """
    elapsed = 0
    while elapsed < timeout_ms:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            return False
        elapsed += poll_ms
        active = _current_section_label(page)
        if active is not None and active != current_section:
            return True
        # If the user reached the review/signature step, treat that as
        # advancing even if the heading hasn't been read yet.
        try:
            if _looks_like_review_page(_safe_body_text(page)):
                return True
        except Exception:
            pass
    return False


def _complete_application_flow(
    page,
    job: AutoApplyJob,
    profile: ApplicationProfile,
    submit: bool,
    timeout_ms: int,
    debug_dump_dir: Path | None,
    max_steps: int = 12,
) -> AutoApplyResult:
    last_section_label: str | None = None
    repeated_section_count = 0
    unknown_section_streak = 0
    for step_index in range(max_steps):
        section_label = _current_section_label(page)
        print(
            f"[auto-apply] Step {step_index + 1}: section = {section_label!r}.",
            flush=True,
        )

        # Don't blindly click Next when we don't even know what page we're on.
        # Two unknown steps in a row means our detectors aren't matching
        # anything we recognise — dump debug state and ask the user to take
        # over instead of clicking through unknown screens.
        if section_label is None:
            unknown_section_streak += 1
            if unknown_section_streak >= 2:
                _write_debug_dump(page, debug_dump_dir, job.id, "section_unknown")
                print(
                    "[auto-apply] Section is unrecognised on two consecutive"
                    " steps. Pausing so you can complete the application"
                    " manually. (A debug dump was saved.)",
                    flush=True,
                )
                advanced = _wait_for_user_to_advance(page, "unknown")
                if not advanced:
                    return AutoApplyResult(
                        job.id,
                        False,
                        False,
                        True,
                        "Could not identify the current Workday section."
                        " Stopped to avoid clicking Next on unknown screens.",
                    )
                last_section_label = None
                repeated_section_count = 0
                unknown_section_streak = 0
                continue
        else:
            unknown_section_streak = 0

        # Detect 'Next was clicked but Workday silently kept us on the
        # same section' (e.g. My Experience with an empty required
        # Source dropdown). Hand control to the user instead of looping.
        if section_label is not None and section_label == last_section_label:
            repeated_section_count += 1
            if repeated_section_count >= 1:
                print(
                    f"[auto-apply] Paused on '{section_label}': Workday did not"
                    " advance after clicking Next. Please fill the remaining"
                    " required fields (Source, Country, Degree, etc.) and click"
                    " Save and Continue. The tool will resume automatically.",
                    flush=True,
                )
                advanced = _wait_for_user_to_advance(page, section_label)
                if not advanced:
                    _write_debug_dump(page, debug_dump_dir, job.id, "section_stuck_timeout")
                    return AutoApplyResult(
                        job.id,
                        False,
                        False,
                        True,
                        f"Timed out waiting for the user to advance past '{section_label}'.",
                    )
                page.wait_for_timeout(1_000)
                last_section_label = None
                repeated_section_count = 0
                continue
        else:
            repeated_section_count = 0
        last_section_label = section_label

        section_result = _fill_known_section(page, job, profile, timeout_ms)
        if not section_result.ok:
            return AutoApplyResult(job.id, False, False, True, section_result.message or "Manual review needed.")

        body_text = _safe_body_text(page)
        if _looks_like_review_page(body_text):
            if not _check_review_signature(page, timeout_ms):
                return AutoApplyResult(job.id, False, False, True, "Review signature checkbox was not found.")
            if not submit:
                _write_debug_dump(page, debug_dump_dir, job.id, "filled_review_not_submitted")
                return AutoApplyResult(
                    job.id,
                    True,
                    False,
                    True,
                    "Application filled through Review. Stopped before final submit because --submit was not provided.",
                )
            if _click_by_role(page, "button", r"\bsubmit\b", timeout_ms):
                page.wait_for_timeout(1_000)
                if _page_has_errors(page):
                    return AutoApplyResult(job.id, False, False, True, "Submit was blocked by required fields.")
                return AutoApplyResult(job.id, True, True, False, "Application submitted by auto-apply.")
            return AutoApplyResult(job.id, False, False, True, "Submit button was not found on Review.")

        # On sections that the resume parser pre-fills (My Experience), Workday
        # often still requires manual verification of dropdowns the parser
        # cannot supply (Source, Country, Degree, etc.). Hand control back to
        # the user, wait for them to click Save and Continue themselves, then
        # resume auto-filling the remaining sections.
        if section_label in MANUAL_ADVANCE_SECTIONS:
            print(
                f"[auto-apply] Paused on '{section_label}'. Please review the"
                " pre-filled fields, fix any required dropdowns, and click"
                " Save and Continue. The tool will resume automatically.",
                flush=True,
            )
            advanced = _wait_for_user_to_advance(page, section_label)
            if not advanced:
                _write_debug_dump(page, debug_dump_dir, job.id, "manual_section_timeout")
                return AutoApplyResult(
                    job.id,
                    False,
                    False,
                    True,
                    f"Timed out waiting for the user to advance past the '{section_label}' section.",
                )
            # Give Workday a moment to render the next section before the next
            # iteration tries to fill it.
            page.wait_for_timeout(1_000)
            continue

        if not _click_by_role(page, "button", r"\b(next|continue|review|save and continue)\b", timeout_ms):
            return AutoApplyResult(job.id, False, False, True, "Next/Continue/Review button was not found.")
        print(
            f"[auto-apply] Clicked Next on '{section_label}'. Waiting for the next section to load.",
            flush=True,
        )

        page.wait_for_timeout(1_500)
        # Only treat error markers as a hard stop if Workday actually flagged
        # invalid form fields. The substring 'error' alone produced false
        # positives because Workday boilerplate ('an error has occurred' help
        # text, etc.) appears on legitimate pages.
        if _page_has_blocking_errors(page):
            return AutoApplyResult(job.id, False, False, True, "Workday shows required fields or validation errors.")

    return AutoApplyResult(job.id, False, False, True, "Reached the application step limit before Review.")


def _fill_known_section(
    page,
    job: AutoApplyJob,
    profile: ApplicationProfile,
    timeout_ms: int,
) -> SectionResult:
    # Use the heading-based label as the primary dispatch — Workday's
    # left sidebar lists every step on every page, so a body-text scan
    # cannot tell us which page we're really on.
    section_label = _current_section_label(page)
    body_text = _safe_body_text(page).lower()

    # Test/legacy callers that pass a fake page may not have a heading;
    # fall back to substring matching when the heading lookup yields None.
    if section_label is None:
        section_label = _section_from_text(body_text)

    if section_label == "my experience":
        # Workday's resume parser pre-fills Work Experience, Education, and
        # the Resume/CV subsection from the Quick Apply upload. Do NOT
        # touch the upload again here.
        return SectionResult(True)

    if section_label == "quick apply":
        if not _upload_resume(page, job.resume_path, timeout_ms):
            return SectionResult(False, "Resume upload field was not found on Quick Apply.")
        # Hard verification: don't advance past Quick Apply unless the
        # resume actually attached. Workday surfaces an attachment chip
        # (delete-file / file-uploaded) once the upload completed.
        if not _wait_for_resume_attached(page, job.resume_path, timeout_ms):
            return SectionResult(
                False,
                "Resume upload did not complete on Quick Apply (no attachment"
                " indicator appeared). Stopped before advancing.",
            )
        print(
            f"[auto-apply] Quick Apply: resume '{job.resume_path.name}' attached.",
            flush=True,
        )
        return SectionResult(True)

    if section_label == "application questions" or "application questions" in body_text:
        _answer_dropdown_by_question(
            page,
            r"eligible to work in the united states without asu sponsorship",
            profile.work_authorization,
            timeout_ms,
        )
        _answer_dropdown_by_question(
            page,
            r"enrolled in class\(es\) at asu",
            profile.enrolled_at_asu,
            timeout_ms,
        )
        _answer_dropdown_by_question(
            page,
            r"eligible for federal work study",
            profile.federal_work_study,
            timeout_ms,
        )
        _answer_dropdown_by_question(
            page,
            r"18 years or older",
            profile.age_18_or_older,
            timeout_ms,
        )
        return SectionResult(True)

    if section_label in {"voluntary personal information", "voluntary disclosures"} or "hispanic or latino" in body_text:
        _answer_radio_by_question(page, r"hispanic or latino", profile.hispanic_or_latino, timeout_ms)
        return SectionResult(True)

    if section_label in {"self identify", "self-identification of disability"} or "cc-305" in body_text:
        _fill_disability_section(page, profile, timeout_ms)
        return SectionResult(True)

    if section_label == "review" or _looks_like_review_page(body_text):
        _check_review_signature(page, timeout_ms)
        return SectionResult(True)

    return SectionResult(True)


def _click_apply(page, timeout_ms: int) -> bool:
    return _click_by_role(page, "button", r"\bapply\b", timeout_ms) or _click_by_role(
        page, "link", r"\bapply\b", timeout_ms
    ) or _click_first_locator(
        page,
        [
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "[role='button']:has-text('Apply')",
            "[data-automation-id='applyButton']",
            "[data-automation-id='adventureButton']",
        ],
        timeout_ms,
    )


def _open_selected_job_from_results(page, job: AutoApplyJob, timeout_ms: int, max_scrolls: int = 6) -> bool:
    for _ in range(max_scrolls):
        if _click_matching_prompt_option(page, job, timeout_ms):
            return True
        try:
            page.mouse.wheel(0, 1_600)
            page.wait_for_timeout(500)
        except Exception:
            break
    return False


def _click_matching_prompt_option(page, job: AutoApplyJob, timeout_ms: int) -> bool:
    if not job.title:
        return False

    try:
        options = page.locator(
            "[data-automation-id='promptOption'][role='link'], [data-automation-id='promptOption']"
        )
        count = options.count()
    except Exception:
        return False

    title_key = job.title.strip().lower()
    title_matches = []
    for index in range(count):
        option = options.nth(index)
        try:
            option_text = option.inner_text(timeout=1_000).strip()
        except Exception:
            continue

        if option_text.lower() != title_key:
            continue

        title_matches.append(option)

        if job.workday_id and _ancestor_contains_text(option, job.workday_id):
            option.click(timeout=timeout_ms)
            return True

    if len(title_matches) == 1:
        title_matches[0].click(timeout=timeout_ms)
        return True

    return False


def _ancestor_contains_text(locator, text: str) -> bool:
    for depth in range(1, 7):
        try:
            ancestor = locator.locator(f"xpath=ancestor::*[{depth}]")
            ancestor_text = ancestor.inner_text(timeout=1_000)
            if text in ancestor_text:
                return True
        except Exception:
            continue

    try:
        return locator.evaluate(
            """
            (node, targetText) => {
              let current = node;
              for (let i = 0; i < 8 && current; i += 1) {
                if ((current.innerText || '').includes(targetText)) return true;
                current = current.parentElement;
              }
              return false;
            }
            """,
            text,
        )
    except Exception:
        return False


def _looks_like_later_step(body_text: str) -> bool:
    """True when the page is past the Quick Apply step.

    The Quick Apply heading text appears in Workday's left-side navigation
    sidebar on every later step. We use the *content* headings unique to
    later steps to disambiguate.
    """
    later_markers = (
        "my experience",
        "application questions",
        "voluntary disclosures",
        "voluntary personal information",
        "self identify",
        "self-identification of disability",
        "legal equivalent of a signature",
    )
    return any(marker in body_text for marker in later_markers)


def _upload_resume(page, resume_path: Path, timeout_ms: int) -> bool:
    # Caller (_fill_known_section) already guarantees this is the Quick
    # Apply step and not a later step, so we never need to worry about
    # auto-filled Work Experience / Education cards on My Experience.
    #
    # Only skip the upload when the resume is *actually attached* to this
    # Quick Apply step. The signal for that is an explicit attachment
    # indicator on the page — a Remove/Delete control or Workday's
    # ``file-uploaded`` widget — paired with the file name. The plain
    # body-text mention is unreliable because Workday's Quick Apply step
    # also lists previously-saved resumes in a "Recent" / "Use a previous
    # resume" section, which would otherwise short-circuit the upload
    # before it ever happens.
    if _resume_already_attached(page, resume_path):
        return True

    if _set_first_file_input(page, resume_path, timeout_ms):
        return True

    _click_first_locator(
        page,
        [
            "button:has-text('Upload')",
            "button:has-text('Select files')",
            "button:has-text('Choose File')",
            "[role='button']:has-text('Upload')",
            "[data-automation-id='file-upload-button']",
        ],
        timeout_ms,
    )
    page.wait_for_timeout(500)
    return _set_first_file_input(page, resume_path, timeout_ms)


def _wait_for_resume_attached(
    page,
    resume_path: Path,
    timeout_ms: int,
    poll_ms: int = 500,
) -> bool:
    """Block until Workday shows an attachment indicator, or time out.

    Used right after _upload_resume to verify the upload finished. Workday
    posts the file asynchronously, so we poll for the attachment chip
    instead of trusting the input.set_input_files call alone.
    """
    elapsed = 0
    deadline = max(timeout_ms, 5_000)
    while elapsed < deadline:
        if _resume_already_attached(page, resume_path):
            return True
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            return False
        elapsed += poll_ms
    return False


def _resume_already_attached(page, resume_path: Path) -> bool:
    """Return True only when the resume is *attached* to this step.

    Workday signals a successfully attached file with one of:
      * an attachment chip with data-automation-id 'file-uploaded',
      * a Delete button with data-automation-id 'delete-file',
      * a hidden <input type='file'> whose .files list is non-empty.

    A plain text mention of the file name in the page body is **not**
    sufficient — Workday's Quick Apply step lists previously-saved
    resumes in a 'Recent files' section even when nothing is attached.
    """
    try:
        for selector in (
            "[data-automation-id='file-uploaded']",
            "[data-automation-id='delete-file']",
            "[data-automation-id='attachments-list'] [data-automation-id='file-uploaded']",
        ):
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: ask the file input itself whether it has files attached.
    # Playwright doesn't expose .files directly, so we use evaluate.
    try:
        has_file = page.evaluate(
            "() => {\n"
            "  const inputs = document.querySelectorAll(\"input[type='file']\");\n"
            "  for (const i of inputs) {\n"
            "    if (i.files && i.files.length > 0) return true;\n"
            "  }\n"
            "  return false;\n"
            "}"
        )
        if has_file:
            return True
    except Exception:
        pass

    return False


def _resume_already_uploaded(page, resume_path: Path) -> bool:
    return _count_uploaded_resume_mentions(page, resume_path) > 0


def _count_uploaded_resume_mentions(page, resume_path: Path) -> int:
    try:
        body_text = _safe_body_text(page).lower()
        return body_text.count(resume_path.name.lower())
    except Exception:
        return 0


def _remove_uploaded_resume_files(page, timeout_ms: int, max_clicks: int = 5) -> int:
    removed = 0
    for _ in range(max_clicks):
        if not _click_by_role(page, "button", r"\bremove\b", timeout_ms):
            break
        removed += 1
        page.wait_for_timeout(500)
    return removed


def _advance_and_submit(page, timeout_ms: int, max_steps: int = 8) -> SubmitResult:
    for _ in range(max_steps):
        if _page_has_errors(page):
            return SubmitResult(False, "Workday shows required fields or validation errors. Manual review needed.")

        if _click_by_role(page, "button", r"\bsubmit\b", timeout_ms):
            page.wait_for_timeout(1_000)
            if _page_has_errors(page):
                return SubmitResult(False, "Submit was blocked by required fields. Manual review needed.")
            return SubmitResult(True, "Application submitted by auto-apply.")

        if _click_by_role(page, "button", r"\b(next|continue|review)\b", timeout_ms):
            page.wait_for_timeout(1_000)
            continue

        return SubmitResult(False, "No next/review/submit button was found. Manual review needed.")

    return SubmitResult(False, "Reached the navigation step limit before submit. Manual review needed.")


def _answer_dropdown_by_question(
    page,
    question_pattern: str,
    answer: str,
    timeout_ms: int,
) -> bool:
    container = _container_for_question(page, question_pattern, ["[role='combobox']", "button", "input"])
    if container is None:
        return False

    for selector in ["[role='combobox']", "button[aria-haspopup]", "button", "input"]:
        try:
            field = container.locator(selector).first
            if field.count() == 0:
                continue
            field.click(timeout=timeout_ms)
            page.wait_for_timeout(300)
            if _choose_dropdown_answer(page, answer, timeout_ms):
                return True
            try:
                page.keyboard.type(answer)
                page.keyboard.press("Enter")
                return True
            except Exception:
                continue
        except Exception:
            continue
    return False


def _choose_dropdown_answer(page, answer: str, timeout_ms: int) -> bool:
    escaped = re.escape(answer)
    candidates = [
        lambda: page.get_by_role("option", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: page.get_by_role("menuitem", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: page.get_by_text(re.compile(rf"^{escaped}$", re.IGNORECASE)).last,
    ]
    for candidate in candidates:
        try:
            locator = candidate()
            locator.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _answer_radio_by_question(page, question_pattern: str, answer: str, timeout_ms: int) -> bool:
    container = _container_for_question(page, question_pattern, ["[role='radio']", "input[type='radio']"])
    if container is None:
        return False

    escaped = re.escape(answer)
    candidates = [
        lambda: container.get_by_role("radio", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: container.locator(
            f"label:has-text('{answer}') input[type='radio'], input[type='radio'][aria-label*='{answer}']"
        ).first,
        lambda: container.get_by_text(re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
    ]
    for candidate in candidates:
        try:
            locator = candidate()
            locator.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _fill_disability_section(page, profile: ApplicationProfile, timeout_ms: int) -> None:
    _answer_dropdown_by_question(page, r"language", profile.disability_language, timeout_ms)
    _fill_by_label(page, r"^name\b", profile.applicant_name, timeout_ms)
    _fill_by_label(page, r"^date\b", profile.today_for_workday(), timeout_ms)
    _check_by_label(page, profile.disability_status, timeout_ms)


def _fill_by_label(page, label_pattern: str, value: str, timeout_ms: int) -> bool:
    try:
        field = page.get_by_label(re.compile(label_pattern, re.IGNORECASE)).first
        field.fill(value, timeout=timeout_ms)
        return True
    except Exception:
        return False


def _check_by_label(page, label_pattern: str, timeout_ms: int) -> bool:
    try:
        checkbox = page.get_by_label(re.compile(label_pattern, re.IGNORECASE)).first
        checkbox.check(timeout=timeout_ms)
        return True
    except Exception:
        pass

    try:
        checkbox = page.get_by_text(re.compile(label_pattern, re.IGNORECASE)).first.locator(
            "xpath=ancestor::*[.//input[@type='checkbox'] or .//*[@role='checkbox']][1]"
        ).locator("input[type='checkbox'], [role='checkbox']").first
        checkbox.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _check_review_signature(page, timeout_ms: int) -> bool:
    return _check_by_label(page, r"legal equivalent of a signature", timeout_ms)


def _looks_like_review_page(text: str) -> bool:
    lowered = text.lower()
    return "legal equivalent of a signature" in lowered or (
        "review" in lowered and "submit" in lowered and "terms" in lowered
    )


def _container_for_question(page, question_pattern: str, field_selectors: list[str]):
    question = page.get_by_text(re.compile(question_pattern, re.IGNORECASE)).first
    selector_predicate = " or ".join(f".//{_xpath_selector(selector)}" for selector in field_selectors)
    xpaths = [
        f"xpath=ancestor::*[{selector_predicate}][1]",
        "xpath=ancestor::*[1]",
        "xpath=ancestor::*[2]",
    ]
    for xpath in xpaths:
        try:
            container = question.locator(xpath)
            if container.count() > 0:
                return container.first
        except Exception:
            continue
    return None


def _xpath_selector(selector: str) -> str:
    if selector == "[role='combobox']":
        return "*[@role='combobox']"
    if selector == "[role='radio']":
        return "*[@role='radio']"
    if selector == "input[type='radio']":
        return "input[@type='radio']"
    if selector == "input":
        return "input"
    if selector == "button":
        return "button"
    return "*"


def _click_by_role(page, role: str, name_pattern: str, timeout_ms: int) -> bool:
    try:
        locator = page.get_by_role(role, name=re.compile(name_pattern, re.IGNORECASE)).first
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _click_first_locator(page, selectors: Iterable[str], timeout_ms: int) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _set_first_file_input(page, resume_path: Path, timeout_ms: int) -> bool:
    try:
        file_input = page.locator("input[type='file']").first
        if file_input.count() == 0:
            return False
        file_input.set_input_files(str(resume_path), timeout=timeout_ms)
        return True
    except Exception:
        return False


def _page_has_errors(page) -> bool:
    try:
        invalid_count = page.locator("[aria-invalid='true']").count()
        if invalid_count > 0:
            return True
        body_text = _safe_body_text(page).lower()
        error_markers = [
            "required field",
            "is required",
            "please complete",
            "please answer",
            "error",
        ]
        return any(marker in body_text for marker in error_markers)
    except Exception:
        return False


def _page_has_blocking_errors(page) -> bool:
    """Tighter check for Workday validation errors.

    Only flags the page as blocked when Workday actually marks form fields with
    ``aria-invalid='true'`` or shows a validation banner. Plain occurrences of
    the substring 'error' or generic 'required' wording are ignored to avoid
    false positives on the My Experience and Voluntary Disclosures pages.
    """
    try:
        invalid_count = page.locator("[aria-invalid='true']").count()
        if invalid_count > 0:
            return True
        try:
            banner_count = page.locator(
                "[role='alert'], [data-automation-id='errorMessage'],"
                " [data-automation-id='formErrors']"
            ).count()
            if banner_count > 0:
                return True
        except Exception:
            pass
        body_text = _safe_body_text(page).lower()
        strict_markers = [
            "please correct the errors",
            "errors found",
            "the following errors",
        ]
        return any(marker in body_text for marker in strict_markers)
    except Exception:
        return False


def _safe_body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""


def _extract_applied_marker(text: str) -> str | None:
    match = re.search(
        r"\bapplied\s+\d{1,2}/\d{1,2}/\d{4}\s*,\s*\d{1,2}:\d{2}\s*(?:AM|PM)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(0)
    return None


def _write_debug_dump(page, debug_dump_dir: Path | None, job_id: int, reason: str) -> None:
    if debug_dump_dir is None:
        return
    debug_dump_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", reason).strip("_")
    base_path = debug_dump_dir / f"auto_apply_job_{job_id}_{safe_reason}"
    try:
        base_path.with_suffix(".txt").write_text(_safe_body_text(page), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(base_path.with_suffix(".png")), full_page=True)
    except Exception:
        pass
