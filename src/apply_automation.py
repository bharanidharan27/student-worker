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
MANUAL_ADVANCE_SECTIONS = ("my experience",)

# How long to wait for the user to manually advance past a manual section before
# giving up (in milliseconds). Default: 15 minutes.
MANUAL_ADVANCE_TIMEOUT_MS = 15 * 60 * 1_000
MANUAL_ADVANCE_POLL_MS = 1_000


def _current_section_label(body_text: str) -> str | None:
    lowered = body_text.lower()
    for label in (
        "quick apply",
        "my experience",
        "application questions",
        "voluntary disclosures",
        "voluntary personal information",
        "self identify",
        "self-identification of disability",
        "review",
    ):
        if label in lowered:
            return label
    return None


def _wait_for_user_to_advance(
    page,
    current_section: str,
    timeout_ms: int = MANUAL_ADVANCE_TIMEOUT_MS,
    poll_ms: int = MANUAL_ADVANCE_POLL_MS,
) -> bool:
    """Poll until the active Workday section changes away from ``current_section``.

    Returns True when the section advanced, False when the timeout elapsed.
    """
    elapsed = 0
    while elapsed < timeout_ms:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            return False
        elapsed += poll_ms
        body_text = _safe_body_text(page).lower()
        if current_section not in body_text:
            return True
        # If the user already moved to the review/signature step, treat that
        # as advancing even if the sidebar still mentions the prior label.
        if _looks_like_review_page(body_text):
            return True
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
    for _ in range(max_steps):
        body_text = _safe_body_text(page)
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
        section_label = _current_section_label(body_text)
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
    body_text = _safe_body_text(page).lower()

    # The Quick Apply label appears in the left sidebar nav on every step of
    # the application (Quick Apply / My Experience / Application Questions /
    # ...). To tell the *current* step apart from the sidebar, prefer the
    # later-page checks first, and only treat this as Quick Apply when no
    # later-step marker is on the page.
    if "my experience" in body_text:
        # Workday's resume parser pre-fills Work Experience, Education, and
        # the Resume/CV subsection from the Quick Apply upload. Do NOT touch
        # the upload again here — clicking Remove on My Experience nukes the
        # auto-filled cards because those cards each have their own Remove
        # button.
        return SectionResult(True)

    if "quick apply" in body_text and not _looks_like_later_step(body_text):
        if not _upload_resume(page, job.resume_path, timeout_ms):
            return SectionResult(False, "Resume upload field was not found on Quick Apply.")
        return SectionResult(True)

    if "application questions" in body_text:
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

    if "voluntary personal information" in body_text or "hispanic or latino" in body_text:
        _answer_radio_by_question(page, r"hispanic or latino", profile.hispanic_or_latino, timeout_ms)
        return SectionResult(True)

    if "self-identification of disability" in body_text or "cc-305" in body_text:
        _fill_disability_section(page, profile, timeout_ms)
        return SectionResult(True)

    if _looks_like_review_page(body_text):
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
    # Only attempt the upload when the Quick Apply step actually has an
    # empty file input. Bailing out the moment we see the file mentioned
    # avoids two scenarios that both delete user data:
    #   1) Re-uploading on My Experience (the page also mentions the file
    #      in the Resume/CV subsection).
    #   2) Calling _remove_uploaded_resume_files indiscriminately, which
    #      clicks every Remove button on the page — including the ones on
    #      auto-filled Work Experience and Education cards.
    if _count_uploaded_resume_mentions(page, resume_path) >= 1:
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
