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

# How long to wait for the Workday virus-scan / upload to complete (ms).
# Workday's async scanner can take 20-30 s on slow connections.
RESUME_UPLOAD_TIMEOUT_MS = 45_000


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
    # Voluntary Disclosures — set per user's self-identification.
    # Keep ethnicity short; Workday may render it with or without the country
    # suffix/parentheses depending on the page state.
    ethnicity: str = "Asian"
    gender: str = "Male"
    veteran_status: str = "Not a Veteran"
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
    min_score: int = 70,
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
        context = browser.new_context(
            storage_state=str(auth_state_path),
            viewport={"width": 1440, "height": 820},
            screen={"width": 1440, "height": 820},
        )
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
# "my experience" is included here because Workday's resume parser pre-fills
# Work Experience and Education from the Quick Apply upload, but required
# dropdowns (To date, Currently work here, Country, Degree, Field of Study)
# are often left empty and must be fixed by the user before clicking Next.
MANUAL_ADVANCE_SECTIONS: tuple[str, ...] = ("my experience",)

# These sections are only safe after the current run has visited Quick Apply.
# If Workday opens a draft in any of them, first return to Quick Apply so the
# resume upload is the first verified action.
SECTIONS_REQUIRING_QUICK_APPLY_FIRST: tuple[str, ...] = (
    "my experience",
    "application questions",
    "voluntary disclosures",
    "voluntary personal information",
    "self identify",
    "self-identification of disability",
    "review",
)

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

    # 1. Heading text. This is the best signal when Workday renders a page
    # title like "Quick Apply" or "My Experience". Body text can include
    # hidden/offscreen fields from other sections, especially in drafts.
    heading = _read_active_section_heading(page)
    if heading is not None:
        match = _section_from_text(heading)
        if match is not None:
            return match

    # 2. Feature detection — useful when Workday hides the left panel or does
    # not expose a clean heading for the active step.
    feature_label = _detect_section_by_features(page)
    if feature_label is not None:
        return feature_label

    # 3. Body-text fallback. Some Workday pages keep stale text from other
    # sections in the body, so do not trust Voluntary Disclosures unless its
    # actual form controls are present.
    try:
        fallback = _section_from_text(_safe_body_text(page))
        if fallback in {"voluntary disclosures", "voluntary personal information"} and not _has_voluntary_disclosure_controls(page):
            return None
        return fallback
    except Exception:
        return None


def _detect_section_by_features(page) -> str | None:
    """Detect the current Workday step by what's actually on the page."""
    try:
        if page.locator("input[type='file']").count() > 0:
            try:
                body_lower_for_upload = _safe_body_text(page).lower()
            except Exception:
                body_lower_for_upload = ""
            if (
                "work experience" not in body_lower_for_upload
                and "education" not in body_lower_for_upload
            ):
                return "quick apply"
    except Exception:
        pass

    try:
        body_lower = _safe_body_text(page).lower()
    except Exception:
        return None

    if _has_quick_apply_content(body_lower):
        return "quick apply"
    if "work experience" in body_lower and "education" in body_lower:
        return "my experience"
    if _has_voluntary_disclosure_content(body_lower) and _has_voluntary_disclosure_controls(page):
        return "voluntary disclosures"
    if "cc-305" in body_lower or "section 503" in body_lower:
        return "self-identification of disability"
    if "i acknowledge" in body_lower or "electronic signature" in body_lower:
        return "review"
    if (
        "eligible to work in the united states" in body_lower
        or "federal work-study" in body_lower
        or "federal work study" in body_lower
    ):
        return "application questions"
    return None


def _section_from_text(text: str) -> str | None:
    lowered = text.lower()
    matches = [label for label in _SECTION_LABELS if label in lowered]
    if len(matches) == 1:
        return matches[0]
    return None


def _read_active_section_heading(page) -> str | None:
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

    for text in candidates:
        labels_in_text = [lbl for lbl in _SECTION_LABELS if lbl in text.lower()]
        if len(labels_in_text) == 1:
            return text

    for text in candidates:
        if text:
            return text
    return None


def _has_voluntary_disclosure_controls(page) -> bool:
    try:
        for selector in (
            "[data-metadata-id='radioButtonSelectList.hispanicOrLatino']",
            "[data-metadata-id='checkBoxSelectList.ethnicityMulti']",
            "[data-metadata-id='dropDownSelectList.genderDropdown']",
            "[data-metadata-id='dropDownSelectList.veteranStatusDropdown']",
            "[data-metadata-id='checkBoxInput.agreementCheckbox']",
            "#checkBoxInput\\.agreementCheckbox",
        ):
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _wait_for_user_to_advance(
    page,
    current_section: str,
    timeout_ms: int = MANUAL_ADVANCE_TIMEOUT_MS,
    poll_ms: int = MANUAL_ADVANCE_POLL_MS,
) -> bool:
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
        try:
            if _looks_like_review_page(_safe_body_text(page)):
                return True
        except Exception:
            pass
    return False


def _wait_for_known_section_label(page, timeout_ms: int = 5_000, poll_ms: int = 500) -> str | None:
    elapsed = 0
    while elapsed < timeout_ms:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            return None
        elapsed += poll_ms
        section_label = _current_section_label(page)
        if section_label is not None:
            return section_label
    return None


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
    quick_apply_started = False
    for step_index in range(max_steps):
        section_label = _current_section_label(page)
        if section_label is None:
            section_label = _wait_for_known_section_label(page)
        print(
            f"[auto-apply] Step {step_index + 1}: section = {section_label!r}.",
            flush=True,
        )

        if section_label is None:
            _write_debug_dump(page, debug_dump_dir, job.id, "section_unknown")
            return AutoApplyResult(
                job.id,
                False,
                False,
                True,
                "Could not identify the current Workday section. "
                "Stopped to avoid clicking Next on an unknown screen.",
            )
        else:
            unknown_section_streak = 0

        if (
            section_label in SECTIONS_REQUIRING_QUICK_APPLY_FIRST
            and not quick_apply_started
        ):
            print(
                f"[auto-apply] Workday opened on '{section_label}'. Returning"
                " to Quick Apply first so the resume upload is verified.",
                flush=True,
            )
            if not _go_to_quick_apply_section(page, timeout_ms):
                _write_debug_dump(page, debug_dump_dir, job.id, "quick_apply_not_first")
                return AutoApplyResult(
                    job.id,
                    False,
                    False,
                    True,
                    "Workday opened past Quick Apply and the tool could not"
                    " return to Quick Apply. Please click Quick Apply in the"
                    " left panel and rerun auto-apply.",
                )
            section_label = "quick apply"
            last_section_label = None
            repeated_section_count = 0

        if section_label is not None and section_label == last_section_label:
            repeated_section_count += 1
            if repeated_section_count >= 1:
                print(
                    f"[auto-apply] Paused on '{section_label}': Workday did not"
                    " advance after clicking Next. Please fill the remaining"
                    " required fields and click Save and Continue."
                    " The tool will resume automatically.",
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

        section_result = _fill_known_section(page, job, profile, timeout_ms, section_label)
        if not section_result.ok:
            return AutoApplyResult(job.id, False, False, True, section_result.message or "Manual review needed.")
        if section_label == "quick apply":
            quick_apply_started = True

        body_text = _safe_body_text(page)
        if section_label == "review" or _looks_like_review_page(body_text):
            if not _check_review_signature(page, timeout_ms):
                print(
                    "[auto-apply] Paused on 'review': could not find the signature"
                    " checkbox automatically. Please tick 'I agree' / 'legal"
                    " equivalent of a signature' and click Submit."
                    " The tool will resume automatically.",
                    flush=True,
                )
                advanced = _wait_for_user_to_advance(page, "review")
                if not advanced:
                    return AutoApplyResult(job.id, False, False, True, "Review signature checkbox was not found.")
                page.wait_for_timeout(1_000)
                last_section_label = None
                repeated_section_count = 0
                continue
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

        # For sections in MANUAL_ADVANCE_SECTIONS (currently "my experience"),
        # pause immediately and wait for the user to review pre-filled fields,
        # fix required dropdowns (To date, Degree, Field of Study, etc.) and
        # click Save and Continue themselves.
        if section_label in MANUAL_ADVANCE_SECTIONS:
            print(
                f"[auto-apply] Paused on '{section_label}'. The resume parser"
                " pre-filled your Work Experience and Education, but some"
                " required fields (To date, Currently work here, Country,"
                " Degree, Field of Study, etc.) may still be empty."
                " Please fill them and click Save and Continue."
                " The tool will resume automatically.",
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
            page.wait_for_timeout(1_000)
            continue

        if not _click_by_role(page, "button", r"\b(next|continue|review|save and continue)\b", timeout_ms):
            return AutoApplyResult(job.id, False, False, True, "Next/Continue/Review button was not found.")
        print(
            f"[auto-apply] Clicked Next on '{section_label}'. Waiting for the next section to load.",
            flush=True,
        )

        page.wait_for_timeout(1_500)
        next_section = _current_section_label(page)
        if next_section in MANUAL_ADVANCE_SECTIONS:
            continue
        if next_section == section_label and _page_has_blocking_errors(page):
            return AutoApplyResult(job.id, False, False, True, "Workday shows required fields or validation errors.")

    return AutoApplyResult(job.id, False, False, True, "Reached the application step limit before Review.")


def _go_to_quick_apply_section(page, timeout_ms: int) -> bool:
    if _current_section_label(page) == "quick apply":
        return True

    quick_apply_pattern = re.compile(r"^quick apply$", re.IGNORECASE)
    click_candidates = [
        lambda: page.get_by_role("link", name=quick_apply_pattern).first,
        lambda: page.get_by_role("button", name=quick_apply_pattern).first,
        lambda: page.get_by_text(quick_apply_pattern).first,
        lambda: page.get_by_text(quick_apply_pattern).last,
    ]

    for candidate in click_candidates:
        try:
            candidate().click(timeout=timeout_ms, force=True)
            page.wait_for_timeout(1_000)
            if _current_section_label(page) == "quick apply":
                return True
        except Exception:
            continue
    return False


def _fill_known_section(
    page,
    job: AutoApplyJob,
    profile: ApplicationProfile,
    timeout_ms: int,
    section_label: str | None = None,
) -> SectionResult:
    body_text = _safe_body_text(page).lower()

    if section_label is None:
        section_label = _current_section_label(page)
    if section_label is None:
        fallback_label = _section_from_text(body_text)
        if fallback_label in {"voluntary disclosures", "voluntary personal information"} and not _has_voluntary_disclosure_controls(page):
            fallback_label = None
        section_label = fallback_label

    if section_label == "my experience":
        # Workday's resume parser pre-fills Work Experience and Education from
        # the Quick Apply upload. MANUAL_ADVANCE_SECTIONS handles the pause.
        return SectionResult(True)

    if section_label == "quick apply":
        if not _upload_resume(page, job.resume_path, timeout_ms):
            return SectionResult(False, "Resume upload field was not found on Quick Apply.")
        # Use RESUME_UPLOAD_TIMEOUT_MS (45 s) — not timeout_ms (10 s) — so the
        # Workday async virus-scan has enough time to finish before we advance.
        if not _wait_for_resume_attached(page, job.resume_path, RESUME_UPLOAD_TIMEOUT_MS):
            return SectionResult(
                False,
                "Resume upload was selected but Workday did not confirm the attachment. "
                "Please verify the resume is attached on Quick Apply, then click Next manually.",
            )
        print(
            f"[auto-apply] Quick Apply: resume '{job.resume_path.name}' attached.",
            flush=True,
        )
        return SectionResult(True)

    if section_label == "application questions" or (
        section_label is None and _has_application_questions_content(body_text)
    ):
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

    if section_label in {"voluntary personal information", "voluntary disclosures"} or (
        section_label is None
        and _has_voluntary_disclosure_content(body_text)
        and _has_voluntary_disclosure_controls(page)
    ):
        if not _fill_voluntary_disclosures(page, profile, timeout_ms):
            return SectionResult(
                False,
                "Could not fill all Voluntary Disclosures fields automatically. "
                "Please fill the highlighted fields manually and click Next.",
            )
        return SectionResult(True)

    if section_label in {"self identify", "self-identification of disability"} or (
        section_label is None and _has_disability_self_id_content(body_text)
    ):
        if not _fill_disability_section(page, profile, timeout_ms):
            return SectionResult(
                False,
                "Could not fill all Self Identify fields automatically. "
                "Please fill the highlighted fields manually and click Next.",
            )
        return SectionResult(True)

    if section_label == "review" or _looks_like_review_page(body_text):
        _check_review_signature(page, timeout_ms)
        return SectionResult(True)

    return SectionResult(True)


def _fill_voluntary_disclosures(page, profile: ApplicationProfile, timeout_ms: int) -> bool:
    """Fill ASU Workday's Voluntary Disclosures page and verify the fields.

    Workday keeps these widgets in stable ``data-metadata-id`` containers even
    when generated ids/classes change, so we target those containers directly.
    """
    print("[auto-apply] Filling Voluntary Disclosures required fields.", flush=True)

    hispanic_ok = _select_workday_labeled_input(
        page,
        "radioButtonSelectList.hispanicOrLatino",
        profile.hispanic_or_latino,
        "radio",
        timeout_ms,
    ) or _answer_radio_by_question(page, r"hispanic or latino", profile.hispanic_or_latino, timeout_ms)
    print(
        f"[auto-apply] Voluntary Disclosures Hispanic/Latino: "
        f"{'selected' if hispanic_ok else 'not selected'} ({profile.hispanic_or_latino}).",
        flush=True,
    )

    ethnicity_ok = _select_workday_labeled_input(
        page,
        "checkBoxSelectList.ethnicityMulti",
        profile.ethnicity,
        "checkbox",
        timeout_ms,
    ) or _check_ethnicity_checkbox(page, profile.ethnicity, timeout_ms)
    print(
        f"[auto-apply] Voluntary Disclosures ethnicity: "
        f"{'selected' if ethnicity_ok else 'not selected'} ({profile.ethnicity}).",
        flush=True,
    )

    gender_ok = _answer_dropdown_by_metadata(
        page,
        "dropDownSelectList.genderDropdown",
        profile.gender,
        timeout_ms,
    )
    print(
        f"[auto-apply] Voluntary Disclosures gender: "
        f"{'verified' if gender_ok else 'not verified'} ({profile.gender}).",
        flush=True,
    )

    veteran_ok = _answer_dropdown_by_metadata(
        page,
        "dropDownSelectList.veteranStatusDropdown",
        profile.veteran_status,
        timeout_ms,
    )
    print(
        f"[auto-apply] Voluntary Disclosures veteran status: "
        f"{'verified' if veteran_ok else 'not verified'} ({profile.veteran_status}).",
        flush=True,
    )

    terms_ok = _tick_agreement_checkbox(page, timeout_ms)
    print(
        f"[auto-apply] Voluntary Disclosures terms agreement: "
        f"{'checked' if terms_ok else 'not checked'}.",
        flush=True,
    )
    try:
        page.wait_for_timeout(700)
    except Exception:
        pass

    missing = _missing_voluntary_disclosure_fields(page, profile)
    if not missing:
        print("[auto-apply] Voluntary Disclosures: all required fields are filled.", flush=True)
        return True

    print(
        "[auto-apply] Voluntary Disclosures still missing: " + ", ".join(missing),
        flush=True,
    )
    return False


def _select_workday_labeled_input(
    page,
    metadata_id: str,
    label_text: str,
    input_type: str,
    timeout_ms: int,
) -> bool:
    try:
        container = page.locator(f"[data-metadata-id='{metadata_id}']").first
        if container.count() == 0:
            return False
        labels = container.locator("label")
        label_count = labels.count()
        for index in range(label_count):
            label = labels.nth(index)
            try:
                text = label.inner_text(timeout=500)
            except Exception:
                continue
            if not _normalised_label_matches(text, label_text):
                continue
            try:
                label.scroll_into_view_if_needed(timeout=timeout_ms)
            except Exception:
                pass
            try:
                label.click(timeout=timeout_ms, force=True)
            except Exception:
                for_id = ""
                try:
                    for_id = label.get_attribute("for") or ""
                except Exception:
                    pass
                if not for_id:
                    continue
                page.locator(f"#{for_id}").first.click(timeout=timeout_ms, force=True)
            print(f"[auto-apply] Selected {metadata_id}: {text.strip()}", flush=True)
            return True
    except Exception:
        pass
    return _click_labeled_input_by_text(page, label_text, input_type, timeout_ms)


def _missing_voluntary_disclosure_fields(page, profile: ApplicationProfile) -> list[str]:
    try:
        return page.evaluate(
            r"""
            ({ gender, veteranStatus }) => {
              const norm = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const selectedText = (metadataId) => {
                const root = document.querySelector(`[data-metadata-id="${metadataId}"]`);
                const selected = root?.querySelector('[data-automation-id="selectSelectedOption"]');
                return norm(selected?.textContent);
              };
              const checkedIn = (metadataId, selector) => {
                const root = document.querySelector(`[data-metadata-id="${metadataId}"]`);
                return !!root?.querySelector(selector);
              };
              const labelCheckboxChecked = (needle) => {
                const target = norm(needle);
                for (const label of document.querySelectorAll('label')) {
                  if (!norm(label.textContent).includes(target)) {
                    continue;
                  }
                  const forId = label.getAttribute('for');
                  const input = label.control || (forId ? document.getElementById(forId) : null);
                  if (input?.type === 'checkbox' && input.checked) {
                    return true;
                  }
                }
                return false;
              };
              const missing = [];
              if (!checkedIn('radioButtonSelectList.hispanicOrLatino', 'input[type="radio"]:checked')) {
                missing.push('Hispanic/Latino');
              }
              if (!checkedIn('checkBoxSelectList.ethnicityMulti', 'input[type="checkbox"]:checked')) {
                missing.push('ethnicity');
              }
              const selectedGender = selectedText('dropDownSelectList.genderDropdown');
              const selectedVeteran = selectedText('dropDownSelectList.veteranStatusDropdown');
              const expectedGender = (gender || '').toLowerCase();
              const expectedVeteran = (veteranStatus || '').toLowerCase();
              if (!selectedGender || selectedGender === 'select one' || !selectedGender.includes(expectedGender)) {
                missing.push('gender');
              }
              if (!selectedVeteran || selectedVeteran === 'select one' || !selectedVeteran.includes(expectedVeteran)) {
                missing.push('veteran status');
              }
              if (
                !checkedIn('checkBoxInput.agreementCheckbox', 'input[type="checkbox"]:checked')
                && !labelCheckboxChecked('legal equivalent of a signature')
              ) {
                missing.push('terms agreement');
              }
              return missing;
            }
            """,
            {"gender": profile.gender, "veteranStatus": profile.veteran_status},
        )
    except Exception as exc:
        print(f"[auto-apply] Voluntary Disclosures verifier error: {exc}", flush=True)
        return _missing_voluntary_disclosure_fields_by_locator(page, profile)


def _missing_voluntary_disclosure_fields_by_locator(page, profile: ApplicationProfile) -> list[str]:
    missing: list[str] = []
    try:
        if page.locator("[data-metadata-id='radioButtonSelectList.hispanicOrLatino'] input[type='radio']:checked").count() == 0:
            missing.append("Hispanic/Latino")
    except Exception:
        missing.append("Hispanic/Latino")
    try:
        if page.locator("[data-metadata-id='checkBoxSelectList.ethnicityMulti'] input[type='checkbox']:checked").count() == 0:
            missing.append("ethnicity")
    except Exception:
        missing.append("ethnicity")
    if not _dropdown_selected_matches(page, "dropDownSelectList.genderDropdown", profile.gender):
        missing.append("gender")
    if not _dropdown_selected_matches(page, "dropDownSelectList.veteranStatusDropdown", profile.veteran_status):
        missing.append("veteran status")
    if not _terms_agreement_is_checked(page):
        missing.append("terms agreement")
    return missing


def _tick_agreement_checkbox(page, timeout_ms: int) -> bool:
    """Tick the Terms & Conditions agreement checkbox on the Voluntary Disclosures page.

    Workday uses data-automation-id='checkBoxInput.agreementCheckbox' for this
    element.  The visible label text is:
      "I understand that checking this box is the legal equivalent of a
       signature accepting the terms above"

    We try three selectors in order of specificity so we always find it even if
    Workday changes the generated element IDs.
    """
    if _check_checkbox_by_label_dom(page, "legal equivalent of a signature", timeout_ms):
        print("[auto-apply] Ticked Terms & Conditions agreement checkbox.", flush=True)
        return True

    # 1. Direct data-automation-id selector (most reliable)
    try:
        cb = page.locator(
            "#checkBoxInput\\.agreementCheckbox input[type='checkbox'],"
            " [data-metadata-id='checkBoxInput.agreementCheckbox'] input[type='checkbox']"
        ).first
        if cb.count() > 0:
            if not cb.is_checked():
                cb.check(timeout=timeout_ms)
                print("[auto-apply] Ticked Terms & Conditions agreement checkbox.", flush=True)
            else:
                print("[auto-apply] Terms & Conditions agreement checkbox already checked.", flush=True)
            return True
    except Exception:
        pass

    # 2. By visible label text
    try:
        cb = page.get_by_label(
            re.compile(r"legal equivalent of a signature", re.IGNORECASE)
        ).first
        if not cb.is_checked():
            cb.check(timeout=timeout_ms)
            print("[auto-apply] Ticked Terms & Conditions agreement checkbox (via label).", flush=True)
        return True
    except Exception:
        pass

    # 3. By the containing div's data-automation-id (fallback)
    try:
        cb = page.locator(
            "[id^='checkBoxInput.agreementCheckbox'][id$='-input'],"
            " input[id*='agreementCheckbox'][type='checkbox']"
        ).first
        if cb.count() > 0:
            if not cb.is_checked():
                cb.check(timeout=timeout_ms)
                print("[auto-apply] Ticked Terms & Conditions agreement checkbox (fallback selector).", flush=True)
            return True
    except Exception:
        pass

    print(
        "[auto-apply] Warning: could not find Terms & Conditions agreement checkbox."
        " If Next is blocked, please tick it manually.",
        flush=True,
    )
    return False


def _terms_agreement_is_checked(page) -> bool:
    try:
        return bool(
            page.evaluate(
                r"""
                () => {
                  const norm = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                  for (const label of document.querySelectorAll('label')) {
                    if (!norm(label.textContent).includes('legal equivalent of a signature')) {
                      continue;
                    }
                    const forId = label.getAttribute('for');
                    const input = label.control || (forId ? document.getElementById(forId) : null);
                    if (input?.type === 'checkbox') {
                      return !!input.checked;
                    }
                  }
                  const input = document.querySelector('[data-metadata-id="checkBoxInput.agreementCheckbox"] input[type="checkbox"], input[id*="agreementCheckbox"][type="checkbox"]');
                  return !!input?.checked;
                }
                """
            )
        )
    except Exception:
        return False


def _check_checkbox_by_label_dom(page, label_contains: str, timeout_ms: int) -> bool:
    try:
        clicked = page.evaluate(
            r"""
            ({ labelContains }) => {
              const norm = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const target = norm(labelContains);
              for (const label of document.querySelectorAll('label')) {
                if (!norm(label.textContent).includes(target)) {
                  continue;
                }
                const forId = label.getAttribute('for');
                const input = label.control || (forId ? document.getElementById(forId) : null);
                if (!input || input.type !== 'checkbox' || input.disabled) {
                  continue;
                }
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                if (!input.checked) {
                  input.click();
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  input.dispatchEvent(new Event('change', { bubbles: true }));
                  input.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                return true;
              }
              return false;
            }
            """,
            {"labelContains": label_contains},
        )
        if clicked:
            page.wait_for_timeout(min(timeout_ms, 500))
            return True
    except Exception:
        pass
    return False


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
    """Block until Workday shows 'Successfully Uploaded!' or a delete chip.

    Workday posts the file asynchronously and runs a virus scan before
    showing the success indicator. Use RESUME_UPLOAD_TIMEOUT_MS (45 s)
    as the deadline — not the generic timeout_ms — so slow virus-scan
    passes don't cause a false 'upload failed' result.
    """
    elapsed = 0
    while elapsed < timeout_ms:
        if _resume_already_attached(page, resume_path):
            return True
        # Also check for the "Successfully Uploaded!" text as an extra signal.
        try:
            body_lower = _safe_body_text(page).lower()
            if "successfully uploaded" in body_lower:
                return True
        except Exception:
            pass
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            return False
        elapsed += poll_ms
    return False


def _resume_already_attached(page, resume_path: Path) -> bool:
    try:
        for selector in (
            "[data-automation-id='file-uploaded']",
            "[data-automation-id='attachments-list'] [data-automation-id='file-uploaded']",
        ):
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
    except Exception:
        pass

    try:
        attached = page.evaluate(
            r"""
            ({ filename }) => {
              const norm = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const target = norm(filename);
              const visible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const attachmentSelector = [
                '[data-automation-id="file-uploaded"]',
                '[data-automation-id="attachments-list"]',
                '[data-automation-id="delete-file"]',
                '[data-automation-id="removeFile"]'
              ].join(',');
              for (const element of document.querySelectorAll(attachmentSelector)) {
                if (!visible(element)) continue;
                const container = element.closest('[data-automation-id="attachments-list"], [data-automation-id="file-uploaded"], li, tr, [role="listitem"], div');
                const text = norm(container?.textContent || element.textContent);
                if (!text.includes(target)) continue;
                if (text.includes('recent') || text.includes('previous resume') || text.includes('use a previous')) continue;
                return true;
              }
              return false;
            }
            """,
            {"filename": resume_path.name},
        )
        if attached:
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


def _answer_dropdown_by_metadata(
    page,
    metadata_id: str,
    answer: str,
    timeout_ms: int,
) -> bool:
    """Select a Workday dropdown by its stable data-metadata-id."""
    if _dropdown_selected_matches(page, metadata_id, answer):
        return True

    option_timeout_ms = min(timeout_ms, 2_000)
    for _ in range(3):
        if not _open_workday_dropdown(page, metadata_id, timeout_ms):
            continue
        if _click_open_workday_option_by_dom(page, metadata_id, answer):
            page.wait_for_timeout(300)
            if _commit_workday_dropdown_selection(page, metadata_id, answer, option_timeout_ms):
                print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
                return True
        _choose_dropdown_answer(page, answer, option_timeout_ms, metadata_id)
        if _commit_workday_dropdown_selection(page, metadata_id, answer, timeout_ms):
            print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
            return True
        _type_into_open_workday_dropdown(page, metadata_id, answer, option_timeout_ms)
        if _commit_workday_dropdown_selection(page, metadata_id, answer, option_timeout_ms):
            print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
            return True
        if _click_open_workday_option_by_dom(page, metadata_id, answer):
            page.wait_for_timeout(300)
            if _commit_workday_dropdown_selection(page, metadata_id, answer, option_timeout_ms):
                print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
                return True
        if _choose_dropdown_answer(page, answer, option_timeout_ms, None):
            if _commit_workday_dropdown_selection(page, metadata_id, answer, option_timeout_ms):
                print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
                return True
        if _dropdown_selected_matches(page, metadata_id, answer):
            print(f"[auto-apply] Selected dropdown value: {answer}", flush=True)
            return True
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass

    options = _visible_workday_dropdown_options(page)
    if options:
        print(
            f"[auto-apply] Could not select dropdown value {answer!r}. "
            f"Visible options: {options}",
            flush=True,
        )
    return False


def _commit_workday_dropdown_selection(page, metadata_id: str, answer: str, timeout_ms: int) -> bool:
    """Nudge Workday to commit/render a dropdown selection after an option click."""
    if _dropdown_selected_matches(page, metadata_id, answer):
        return True

    selectors = [
        f"[data-metadata-id='{metadata_id}']",
        f"[data-metadata-id='{metadata_id}'] [data-automation-id='selectSelectedOption']",
        f"[data-metadata-id='{metadata_id}'] [data-automation-id='selectShowAll']",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            locator.click(timeout=timeout_ms, force=True)
            page.wait_for_timeout(500)
            if _dropdown_selected_matches(page, metadata_id, answer):
                return True
            try:
                page.keyboard.press("Tab")
                page.wait_for_timeout(500)
            except Exception:
                pass
            if _dropdown_selected_matches(page, metadata_id, answer):
                return True
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass
        except Exception:
            continue

    try:
        page.locator("body").click(timeout=timeout_ms, position={"x": 5, "y": 5}, force=True)
        page.wait_for_timeout(500)
    except Exception:
        pass
    return _dropdown_selected_matches(page, metadata_id, answer)


def _open_workday_dropdown(page, metadata_id: str, timeout_ms: int) -> bool:
    selectors = [
        f"[data-metadata-id='{metadata_id}']",
        f"[id^='{metadata_id}-input']",
    ]
    for selector in selectors:
        try:
            field = page.locator(selector).first
            if field.count() == 0:
                continue
            try:
                field.scroll_into_view_if_needed(timeout=timeout_ms)
            except Exception:
                pass
            opener = field.locator("[data-automation-id='selectShowAll']").first
            if opener.count() > 0:
                opener.click(timeout=timeout_ms, force=True)
            else:
                field.click(timeout=timeout_ms, force=True)
            page.wait_for_timeout(700)
            return True
        except Exception:
            continue
    return False


def _dropdown_selected_matches(page, metadata_id: str, answer: str) -> bool:
    try:
        selected = page.evaluate(
            r"""
            ({ metadataId }) => {
              const root = document.querySelector(`[data-metadata-id="${metadataId}"]`);
              const selectedOption = root?.querySelector('[data-automation-id="selectSelectedOption"]');
              return (selectedOption?.textContent || '').trim().toLowerCase();
            }
            """,
            {"metadataId": metadata_id},
        )
        return bool(selected) and answer.lower() in selected
    except Exception:
        return False


def _type_into_open_workday_dropdown(page, metadata_id: str, answer: str, timeout_ms: int) -> bool:
    search_selectors = [
        "[data-automation-id='searchBox'] input",
        "[data-automation-id='searchBox']",
        "input[role='combobox']",
        "input[type='text']",
        "[role='textbox']",
    ]
    for selector in search_selectors:
        try:
            field = page.locator(selector).last
            if field.count() == 0:
                continue
            field.click(timeout=timeout_ms, force=True)
            try:
                field.fill(answer, timeout=timeout_ms)
            except Exception:
                page.keyboard.press("Control+A")
                page.keyboard.type(answer)
            page.wait_for_timeout(700)
            if _choose_dropdown_answer(page, answer, timeout_ms, metadata_id):
                return True
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            if _dropdown_selected_matches(page, metadata_id, answer):
                return True
        except Exception:
            continue

    try:
        page.keyboard.type(answer)
        page.wait_for_timeout(700)
        if _choose_dropdown_answer(page, answer, timeout_ms, metadata_id):
            return True
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        return _dropdown_selected_matches(page, metadata_id, answer)
    except Exception:
        return False


def _click_open_workday_option_by_dom(page, metadata_id: str, answer: str) -> bool:
    try:
        return bool(
            page.evaluate(
                r"""
                ({ answer }) => {
                  const norm = (value) => (value || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .toLowerCase();
                  const target = norm(answer);
                  const selectors = [
                    '[role="option"]',
                    '[role="menuitem"]',
                    '[data-automation-id="promptOption"]',
                    '[data-automation-label]',
                    '.gwt-Label'
                  ];
                  const elements = Array.from(document.querySelectorAll(selectors.join(',')));
                  for (const element of elements) {
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') {
                      continue;
                    }
                    const text = norm(element.getAttribute('data-automation-label') || element.textContent);
                    if (text !== target) {
                      continue;
                    }
                    element.scrollIntoView({ block: 'center', inline: 'nearest' });
                    element.click();
                    return true;
                  }
                  return false;
                }
                """,
                {"answer": answer, "metadataId": metadata_id},
            )
        )
    except Exception:
        return False


def _visible_workday_dropdown_options(page) -> list[str]:
    try:
        values = page.evaluate(
            r"""
            () => {
              const selectors = [
                '[role="option"]',
                '[role="menuitem"]',
                '[data-automation-id="promptOption"]',
                '[data-automation-label]'
              ];
              const seen = new Set();
              const out = [];
              for (const element of document.querySelectorAll(selectors.join(','))) {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') {
                  continue;
                }
                const text = (element.getAttribute('data-automation-label') || element.textContent || '')
                  .replace(/\s+/g, ' ')
                  .trim();
                if (text && !seen.has(text)) {
                  seen.add(text);
                  out.push(text);
                }
              }
              return out.slice(0, 20);
            }
            """
        )
        return list(values or [])
    except Exception:
        return []


def _choose_dropdown_answer(page, answer: str, timeout_ms: int, metadata_id: str | None = None) -> bool:
    escaped = re.escape(answer)
    candidates = [
        lambda: page.locator(f"[data-automation-label='{answer}']").last,
        lambda: page.locator("[data-automation-id='promptOption']").filter(
            has_text=re.compile(rf"^{escaped}$", re.IGNORECASE)
        ).last,
        lambda: page.get_by_role("option", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: page.get_by_role("menuitem", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: page.locator("[role='option'], [role='menuitem'], [data-automation-id='promptOption']").filter(
            has_text=re.compile(rf"^{escaped}$", re.IGNORECASE)
        ).last,
    ]
    for candidate in candidates:
        try:
            locator = candidate()
            locator.click(timeout=timeout_ms, force=True)
            if metadata_id is None:
                return True
            page.wait_for_timeout(500)
            if _dropdown_selected_matches(page, metadata_id, answer):
                return True
        except Exception:
            continue
    try:
        locator = page.get_by_text(re.compile(rf"^{escaped}$", re.IGNORECASE)).last
        locator.click(timeout=timeout_ms, force=True)
        if metadata_id is None:
            return True
        page.wait_for_timeout(500)
        return _dropdown_selected_matches(page, metadata_id, answer)
    except Exception:
        return False


def _answer_radio_by_question(page, question_pattern: str, answer: str, timeout_ms: int) -> bool:
    container = _container_for_question(page, question_pattern, ["[role='radio']", "input[type='radio']"])
    if container is None:
        return False

    escaped = re.escape(answer)
    candidates = [
        lambda: container.get_by_role("radio", name=re.compile(rf"^{escaped}$", re.IGNORECASE)).first,
        lambda: container.locator(
            f"xpath=.//label[normalize-space()='{answer}']"
        ).first,
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
    return _click_labeled_input_by_text(page, answer, "radio", timeout_ms)


def _normalise_ethnicity(label: str) -> str:
    """Strip parentheses and normalise whitespace for ethnicity label comparison.

    Workday's HTML uses "Asian United States of America" (no parentheses) while
    profile values may use "Asian (United States of America)".  Normalising both
    sides makes matching robust to either spelling.
    """
    return re.sub(r"\s+", " ", re.sub(r"[()]", "", label)).strip().lower()


def _normalised_label_matches(label: str, target: str) -> bool:
    label_norm = _normalise_ethnicity(label)
    target_norm = _normalise_ethnicity(target)
    return (
        label_norm == target_norm
        or label_norm.startswith(f"{target_norm} ")
        or target_norm in label_norm
    )


def _check_ethnicity_checkbox(page, ethnicity_label: str, timeout_ms: int) -> bool:
    """Tick one ethnicity checkbox by its visible label text.

    Normalises parentheses so "Asian (United States of America)" matches the
    actual Workday label "Asian United States of America".
    """
    if not ethnicity_label:
        return False

    normalised_target = _normalise_ethnicity(ethnicity_label)

    # 1. Iterate all ethnicity checkboxes and match by normalised label text.
    try:
        checkbox_group = page.locator("[data-automation-id='selectMany']").first
        if checkbox_group.count() > 0:
            labels = checkbox_group.locator("label")
            count = labels.count()
            for i in range(count):
                try:
                    lbl = labels.nth(i)
                    lbl_text = _normalise_ethnicity(lbl.inner_text(timeout=500))
                    if _normalised_label_matches(lbl_text, normalised_target):
                        for_id = lbl.get_attribute("for") or ""
                        if for_id:
                            cb = page.locator(f"#{for_id}").first
                        else:
                            cb = lbl.locator("xpath=preceding-sibling::input[@type='checkbox']").first
                        if cb.count() > 0 and not cb.is_checked():
                            cb.click(timeout=timeout_ms)
                        print(
                            f"[auto-apply] Ticked ethnicity checkbox: {lbl.inner_text(timeout=500).strip()!r}",
                            flush=True,
                        )
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    if _click_labeled_input_by_text(page, ethnicity_label, "checkbox", timeout_ms):
        print(f"[auto-apply] Ticked ethnicity checkbox: {ethnicity_label!r}", flush=True)
        return True

    # 2. Fuzzy fallback: get_by_text with normalised label
    try:
        label_loc = page.get_by_text(
            re.compile(re.escape(normalised_target), re.IGNORECASE)
        ).first
        checkbox = label_loc.locator(
            "xpath=ancestor::*[.//input[@type='checkbox'] or .//*[@role='checkbox']][1]"
        ).locator("input[type='checkbox'], [role='checkbox']").first
        checkbox.click(timeout=timeout_ms)
        return True
    except Exception:
        pass

    # 3. get_by_label fallback (original label, then normalised)
    for attempt_label in (ethnicity_label, normalised_target):
        try:
            page.get_by_label(re.compile(re.escape(attempt_label), re.IGNORECASE)).first.check(timeout=timeout_ms)
            return True
        except Exception:
            continue

    print(
        f"[auto-apply] Warning: could not find ethnicity checkbox for {ethnicity_label!r}."
        " Please tick it manually if required.",
        flush=True,
    )
    return False


def _click_labeled_input_by_text(page, label_text: str, input_type: str, timeout_ms: int) -> bool:
    """Click a radio/checkbox whose visible label matches the desired text."""
    try:
        clicked = page.evaluate(
            r"""
            ({ labelText, inputType }) => {
              const norm = (value) => (value || '')
                .replace(/[()]/g, '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();
              const target = norm(labelText);
              const labels = Array.from(document.querySelectorAll('label'));
              for (const label of labels) {
                const text = norm(label.textContent);
                if (!(text === target || text.startsWith(`${target} `) || text.includes(target))) {
                  continue;
                }
                const forId = label.getAttribute('for');
                const input = label.control
                  || (forId ? document.getElementById(forId) : null)
                  || label.querySelector(`input[type="${inputType}"]`);
                if (!input || input.type !== inputType || input.disabled) {
                  continue;
                }
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                if (inputType === 'checkbox' && input.checked) {
                  return true;
                }
                if (inputType === 'radio' && input.checked) {
                  return true;
                }
                input.click();
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              return false;
            }
            """,
            {"labelText": label_text, "inputType": input_type},
        )
        if clicked:
            page.wait_for_timeout(min(timeout_ms, 500))
            return True
    except Exception:
        pass
    return False


def _fill_disability_section(page, profile: ApplicationProfile, timeout_ms: int) -> bool:
    print("[auto-apply] Filling Self Identify required fields.", flush=True)
    _answer_dropdown_by_question(page, r"language", profile.disability_language, timeout_ms)
    name_ok = _fill_by_label(page, r"^name\b", profile.applicant_name, timeout_ms) or _fill_labeled_text_field_by_text(
        page,
        "Name",
        profile.applicant_name,
        timeout_ms,
    )
    print(
        f"[auto-apply] Self Identify name: {'filled' if name_ok else 'not filled'}.",
        flush=True,
    )
    date_value = profile.today_for_workday()
    date_ok = (
        _fill_self_identify_date(page, date_value, timeout_ms)
        or _fill_by_label(page, r"^date\b", _compact_workday_date(date_value), timeout_ms)
        or _fill_labeled_text_field_by_text(page, "Date", _compact_workday_date(date_value), timeout_ms)
    )
    print(
        f"[auto-apply] Self Identify date: {'filled' if date_ok else 'not filled'} ({date_value}).",
        flush=True,
    )
    disability_ok = (
        _check_disability_no_checkbox(page, timeout_ms)
        or _check_by_label(page, profile.disability_status, min(timeout_ms, 1_500))
        or _click_labeled_input_by_text(
            page,
            profile.disability_status,
            "checkbox",
            min(timeout_ms, 1_500),
        )
    )
    print(
        f"[auto-apply] Self Identify disability status: {'checked' if disability_ok else 'not checked'}.",
        flush=True,
    )
    try:
        page.keyboard.press("Tab")
    except Exception:
        pass
    try:
        page.wait_for_timeout(700)
    except Exception:
        pass

    if "date" in _missing_self_identify_fields(page, profile):
        date_ok = _fill_self_identify_date(page, date_value, timeout_ms)
        print(
            f"[auto-apply] Self Identify date retry: {'filled' if date_ok else 'not filled'}.",
            flush=True,
        )
    if "disability status" in _missing_self_identify_fields(page, profile):
        disability_ok = _check_disability_no_checkbox(page, timeout_ms)
        print(
            f"[auto-apply] Self Identify disability retry: {'checked' if disability_ok else 'not checked'}.",
            flush=True,
        )
    try:
        page.wait_for_timeout(700)
    except Exception:
        pass

    missing = _missing_self_identify_fields(page, profile)
    if not missing:
        print("[auto-apply] Self Identify: all required fields are filled.", flush=True)
        return True
    print("[auto-apply] Self Identify still missing: " + ", ".join(missing), flush=True)
    return False


def _fill_self_identify_date(page, value: str, timeout_ms: int) -> bool:
    """Fill the CC-305 date field, whose Workday date widget is often unlabeled."""
    compact_value = _compact_workday_date(value)
    if _fill_segmented_self_identify_date(page, compact_value, timeout_ms):
        return True
    if _interactive_fill_self_identify_date(page, compact_value, timeout_ms):
        return True
    if _select_self_identify_date_from_picker(page, compact_value, timeout_ms):
        return True

    try:
        filled = page.evaluate(
            r"""
            ({ value }) => {
              const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const visible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const isEmployeeIdContext = (element) => {
                let current = element;
                for (let depth = 0; depth < 4 && current; depth += 1) {
                  const text = norm(current.textContent);
                  if (text.includes('employee id') && !/\bdate\b/.test(text)) return true;
                  current = current.parentElement;
                }
                return false;
              };
              const setValue = (input, nextValue) => {
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                if (descriptor?.set) {
                  descriptor.set.call(input, nextValue);
                } else {
                  input.value = nextValue;
                }
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true }));
              };
              const candidateInputsNearDateLabel = [];
              for (const label of document.querySelectorAll('label')) {
                const text = norm(label.textContent);
                if (!(text === 'date' || text.startsWith('date '))) {
                  continue;
                }
                const labelRect = label.getBoundingClientRect();
                const forId = label.getAttribute('for');
                const controlled = label.control || (forId ? document.getElementById(forId) : null);
                if (controlled && controlled.tagName === 'INPUT') {
                  candidateInputsNearDateLabel.push(controlled);
                }
                let current = label;
                for (let depth = 0; depth < 8 && current; depth += 1) {
                  const input = current.querySelector?.('input:not([type="checkbox"]):not([type="radio"])');
                  if (input) candidateInputsNearDateLabel.push(input);
                  current = current.parentElement;
                }
                let sibling = label.parentElement?.nextElementSibling;
                for (let hops = 0; hops < 4 && sibling; hops += 1) {
                  const input = sibling.querySelector?.('input:not([type="checkbox"]):not([type="radio"])');
                  if (input) candidateInputsNearDateLabel.push(input);
                  sibling = sibling.nextElementSibling;
                }
              }
              const placeholderInputs = Array.from(document.querySelectorAll('input:not([type="checkbox"]):not([type="radio"])'))
                .filter((input) => /m+\s*\/\s*d+\s*\/\s*y+/i.test(input.getAttribute('placeholder') || input.getAttribute('aria-label') || ''));
              for (const input of [...new Set([...candidateInputsNearDateLabel, ...placeholderInputs])]) {
                if (input.disabled || input.readOnly) continue;
                if (!visible(input) || isEmployeeIdContext(input)) continue;
                setValue(input, value);
                return true;
              }
              return false;
            }
            """,
            {"value": compact_value},
        )
        if filled:
            try:
                page.keyboard.press("Tab")
                page.wait_for_timeout(min(timeout_ms, 500))
            except Exception:
                pass
            if _self_identify_date_has_value(page):
                return True
            if _select_self_identify_date_from_picker(page, compact_value, timeout_ms):
                return True
    except Exception:
        pass

    for selector in (
        "input[placeholder*='MM']",
        "input[aria-label*='Date']",
        "input[id*='date']",
        "input[data-automation-id*='date']",
    ):
        try:
            field = page.locator(selector).first
            if field.count() == 0:
                continue
            field.fill(compact_value, timeout=timeout_ms)
            page.keyboard.press("Tab")
            page.wait_for_timeout(min(timeout_ms, 500))
            if _self_identify_date_has_value(page):
                return True
        except Exception:
            continue
    return False


def _compact_workday_date(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _fill_segmented_self_identify_date(page, value: str, timeout_ms: int) -> bool:
    """Fill Workday date controls that expose MM, DD, and YYYY as separate inputs."""
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value)
    if not match:
        return False
    month, day, year = match.groups()
    try:
        filled = page.evaluate(
            r"""
            ({ month, day, year }) => {
              const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const visible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const setValue = (input, nextValue) => {
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                input.focus?.();
                const proto = input instanceof HTMLTextAreaElement
                  ? HTMLTextAreaElement.prototype
                  : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
                if (descriptor?.set) {
                  descriptor.set.call(input, nextValue);
                } else {
                  input.value = nextValue;
                }
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true }));
              };
              for (const label of document.querySelectorAll('label')) {
                const text = norm(label.textContent);
                if (!(text === 'date' || text.startsWith('date '))) {
                  continue;
                }
                const labelRect = label.getBoundingClientRect();
                const candidates = [];
                let current = label.parentElement;
                for (let depth = 0; depth < 7 && current; depth += 1) {
                  candidates.push(...current.querySelectorAll('input:not([type="checkbox"]):not([type="radio"])'));
                  current = current.parentElement;
                }
                let sibling = label.parentElement?.nextElementSibling;
                for (let hops = 0; hops < 5 && sibling; hops += 1) {
                  candidates.push(...sibling.querySelectorAll?.('input:not([type="checkbox"]):not([type="radio"])') || []);
                  if (sibling.matches?.('input:not([type="checkbox"]):not([type="radio"])')) candidates.push(sibling);
                  sibling = sibling.nextElementSibling;
                }
                const fields = [...new Set(candidates)]
                  .filter((input) => {
                    if (!visible(input) || input.disabled || input.readOnly) return false;
                    const rect = input.getBoundingClientRect();
                    if (rect.top + rect.height < labelRect.top - 2) return false;
                    const descriptor = [
                      input.getAttribute('placeholder'),
                      input.getAttribute('aria-label'),
                      input.getAttribute('title'),
                      input.id,
                      input.name,
                      input.getAttribute('data-automation-id')
                    ].map(norm).join(' ');
                    return descriptor.includes('mm')
                      || descriptor.includes('dd')
                      || descriptor.includes('yyyy')
                      || descriptor.includes('date');
                  })
                  .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                if (fields.length >= 3) {
                  setValue(fields[0], month);
                  setValue(fields[1], day);
                  setValue(fields[2], year);
                  return true;
                }
              }
              return false;
            }
            """,
            {"month": month, "day": day, "year": year},
        )
        if filled:
            try:
                page.keyboard.press("Tab")
                page.wait_for_timeout(min(timeout_ms, 500))
            except Exception:
                pass
            return _self_identify_date_has_value(page)
    except Exception:
        pass
    return False


def _interactive_fill_self_identify_date(page, value: str, timeout_ms: int) -> bool:
    if not _focus_self_identify_date_field(page, timeout_ms):
        return False
    try:
        page.keyboard.press("Control+A")
        page.keyboard.type(value)
        page.keyboard.press("Tab")
        page.wait_for_timeout(700)
        if _self_identify_date_has_value(page):
            return True
    except Exception:
        pass
    return _select_self_identify_date_from_picker(page, value, timeout_ms)


def _focus_self_identify_date_field(page, timeout_ms: int) -> bool:
    try:
        focused = page.evaluate(
            r"""
            () => {
              const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const visible = (element) => {
                if (!element) return false;
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const isEmployeeIdContext = (element) => {
                let current = element;
                for (let depth = 0; depth < 4 && current; depth += 1) {
                  const text = norm(current.textContent);
                  if (text.includes('employee id') && !/\bdate\b/.test(text)) return true;
                  current = current.parentElement;
                }
                return false;
              };
              const interactiveSelector = [
                'input:not([type="checkbox"]):not([type="radio"])',
                '[role="textbox"]',
                '[contenteditable="true"]',
                '[data-automation-id*="date"]',
                'button[aria-label*="calendar" i]',
                'button[aria-label*="date" i]'
              ].join(',');
              for (const label of document.querySelectorAll('label')) {
                const text = norm(label.textContent);
                if (!(text === 'date' || text.startsWith('date '))) {
                  continue;
                }
                const labelRect = label.getBoundingClientRect();
                const candidates = [];
                const forId = label.getAttribute('for');
                const controlled = label.control || (forId ? document.getElementById(forId) : null);
                if (controlled) candidates.push(controlled);

                let current = label.parentElement;
                for (let depth = 0; depth < 7 && current; depth += 1) {
                  candidates.push(...current.querySelectorAll(interactiveSelector));
                  current = current.parentElement;
                }

                let sibling = label.parentElement?.nextElementSibling;
                for (let hops = 0; hops < 5 && sibling; hops += 1) {
                  candidates.push(...sibling.querySelectorAll(interactiveSelector));
                  if (sibling.matches?.(interactiveSelector)) candidates.push(sibling);
                  sibling = sibling.nextElementSibling;
                }

                const unique = [...new Set(candidates)].filter((candidate) => {
                  if (!visible(candidate) || candidate.disabled || candidate.readOnly) return false;
                  if (isEmployeeIdContext(candidate)) return false;
                  const rect = candidate.getBoundingClientRect();
                  if (rect.top + rect.height < labelRect.top - 2) return false;
                  const descriptor = [
                    candidate.getAttribute('placeholder'),
                    candidate.getAttribute('aria-label'),
                    candidate.getAttribute('title'),
                    candidate.id,
                    candidate.name,
                    candidate.getAttribute('data-automation-id'),
                    candidate.textContent
                  ].map(norm).join(' ');
                  return /m+\s*\/\s*d+\s*\/\s*y+/.test(descriptor)
                    || /\bdate\b/.test(descriptor)
                    || descriptor.includes('calendar');
                }).sort((a, b) => {
                  const aTag = (a.tagName || '').toLowerCase();
                  const bTag = (b.tagName || '').toLowerCase();
                  const aText = a.matches?.('input, textarea, [role="textbox"], [contenteditable="true"]') ? 0 : 1;
                  const bText = b.matches?.('input, textarea, [role="textbox"], [contenteditable="true"]') ? 0 : 1;
                  if (aText !== bText) return aText - bText;
                  if (aTag !== bTag) return aTag.localeCompare(bTag);
                  return a.getBoundingClientRect().left - b.getBoundingClientRect().left;
                });
                const target = unique[0];
                if (!target) {
                  continue;
                }
                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                target.click();
                target.focus?.();
                return true;
              }
              return false;
            }
            """
        )
        if focused:
            page.wait_for_timeout(min(timeout_ms, 500))
            return True
    except Exception:
        pass
    return False


def _select_self_identify_date_from_picker(page, value: str, timeout_ms: int) -> bool:
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", value)
    if not match:
        return False
    month, day, year = match.groups()
    mmdd = f"{month}{day}"
    month_number = str(int(month))
    selectors = [
        (
            "button[data-automation-id='datePickerDay']"
            f"[data-uxi-datepicker-year='{year}']"
            f"[data-uxi-datepicker-month='{month_number}']"
            f"[data-uxi-datepicker-mmdd='{mmdd}']"
        ),
        (
            "button[data-automation-id='datePickerSelectedToday']"
            f"[data-uxi-datepicker-year='{year}']"
            f"[data-uxi-datepicker-mmdd='{mmdd}']"
        ),
        "button[data-automation-id='datePickerSelectedToday']",
    ]

    for _ in range(2):
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if button.count() == 0:
                    continue
                button.click(timeout=timeout_ms, force=True)
                page.wait_for_timeout(700)
                try:
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                if _self_identify_date_has_value(page):
                    return True
            except Exception:
                continue
        if not _focus_self_identify_date_field(page, timeout_ms):
            break
    return False


def _self_identify_date_has_value(page) -> bool:
    try:
        return bool(
            page.evaluate(
                r"""
                () => {
                  const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
                  const visible = (element) => {
                    if (!element) return false;
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                  };
                  const hasRealDate = (value) => {
                    const text = norm(value);
                    return /\d{1,2}\s*\/\s*\d{1,2}\s*\/\s*\d{4}/.test(text)
                      && !text.includes('mm')
                      && !text.includes('yyyy');
                  };
                  const dateFieldText = [];
                  for (const label of document.querySelectorAll('label')) {
                    const text = norm(label.textContent);
                    if (!(text === 'date' || text.startsWith('date '))) {
                      continue;
                    }
                    const forId = label.getAttribute('for');
                    const controlled = label.control || (forId ? document.getElementById(forId) : null);
                    if (controlled) {
                      dateFieldText.push(controlled.value, controlled.textContent, controlled.getAttribute('aria-label'));
                    }
                    let current = label.parentElement;
                    for (let depth = 0; depth < 7 && current; depth += 1) {
                      if (visible(current)) {
                        dateFieldText.push(current.textContent);
                      }
                      for (const input of current.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]), [role="textbox"]')) {
                        dateFieldText.push(input.value, input.textContent, input.getAttribute('aria-label'), input.getAttribute('aria-valuetext'));
                      }
                      current = current.parentElement;
                    }
                    let sibling = label.parentElement?.nextElementSibling;
                    for (let hops = 0; hops < 5 && sibling; hops += 1) {
                      if (visible(sibling)) {
                        dateFieldText.push(sibling.textContent);
                      }
                      for (const input of sibling.querySelectorAll?.('input:not([type="checkbox"]):not([type="radio"]), [role="textbox"]') || []) {
                        dateFieldText.push(input.value, input.textContent, input.getAttribute('aria-label'), input.getAttribute('aria-valuetext'));
                      }
                      sibling = sibling.nextElementSibling;
                    }
                  }
                  return dateFieldText.some(hasRealDate);
                }
                """
            )
        )
    except Exception:
        return False


def _check_disability_no_checkbox(page, timeout_ms: int) -> bool:
    try:
        checked = page.evaluate(
            r"""
            () => {
              const norm = (value) => (value || '')
                .replace(/[()]/g, '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();
              const target = 'no, i do not have a disability';
              const labels = Array.from(document.querySelectorAll(
                '[data-automation-id="checkbox"] label, label'
              ));
              for (const label of labels) {
                if (!norm(label.textContent).includes(norm(target))) {
                  continue;
                }
                const root = label.closest('[data-automation-id="checkbox"]') || label.parentElement;
                const forId = label.getAttribute('for');
                const input = (forId ? document.getElementById(forId) : null)
                  || root?.querySelector('input[type="checkbox"]');
                if (!input || input.type !== 'checkbox' || input.disabled) {
                  continue;
                }
                if (input.checked || root?.getAttribute('data-automationcheckboxchecked') === 'true') {
                  return true;
                }
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                input.click();
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              return false;
            }
            """
        )
        if checked:
            page.wait_for_timeout(min(timeout_ms, 150))
            return True
    except Exception:
        pass

    return _click_labeled_input_by_text(
        page,
        "No, I do not have a disability",
        "checkbox",
        min(timeout_ms, 1_000),
    )


def _fill_labeled_text_field_by_text(page, label_text: str, value: str, timeout_ms: int) -> bool:
    try:
        filled = page.evaluate(
            r"""
            ({ labelText, value }) => {
              const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const target = norm(labelText);
              const setValue = (input, nextValue) => {
                input.scrollIntoView({ block: 'center', inline: 'nearest' });
                const proto = input instanceof HTMLTextAreaElement
                  ? HTMLTextAreaElement.prototype
                  : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
                if (descriptor?.set) {
                  descriptor.set.call(input, nextValue);
                } else {
                  input.value = nextValue;
                }
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true }));
              };
              for (const label of document.querySelectorAll('label')) {
                const text = norm(label.textContent);
                if (!(text === target || text.startsWith(`${target} `))) {
                  continue;
                }
                const forId = label.getAttribute('for');
                let input = label.control || (forId ? document.getElementById(forId) : null);
                if (!input) {
                  const container = label.closest('li, [data-automation-id="formLabelRequired"], [data-automation-id="decorationWrapper"], div');
                  input = container?.querySelector('input:not([type="checkbox"]):not([type="radio"]), textarea');
                }
                if (!input || input.disabled) {
                  continue;
                }
                setValue(input, value);
                return true;
              }
              return false;
            }
            """,
            {"labelText": label_text, "value": value},
        )
        if filled:
            page.wait_for_timeout(min(timeout_ms, 500))
            return True
    except Exception:
        pass
    return False


def _missing_self_identify_fields(page, profile: ApplicationProfile) -> list[str]:
    try:
        return page.evaluate(
            r"""
            ({ name, disabilityStatus }) => {
              const norm = (input) => (input || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const isTextInput = (input) => input && !['checkbox', 'radio'].includes((input.type || '').toLowerCase());
              const findInputByLabel = (labelText) => {
                const target = norm(labelText);
                for (const label of document.querySelectorAll('label')) {
                  const text = norm(label.textContent);
                  if (!(text === target || text.startsWith(`${target} `))) {
                    continue;
                  }
                  const forId = label.getAttribute('for');
                  let input = label.control || (forId ? document.getElementById(forId) : null);
                  if (!input) {
                    const container = label.closest('li, [data-automation-id="formLabelRequired"], [data-automation-id="decorationWrapper"], div');
                    input = container?.querySelector('input:not([type="checkbox"]):not([type="radio"]), textarea');
                  }
                  if (input) return input;
                }
                return null;
              };
              const findDateInput = () => {
                const labelled = findInputByLabel('Date');
                if (isTextInput(labelled)) {
                  return labelled;
                }
                const dateLike = Array.from(document.querySelectorAll('input'))
                  .find((input) => {
                    if (!isTextInput(input)) return false;
                    const descriptor = [
                      input.getAttribute('placeholder'),
                      input.getAttribute('aria-label'),
                      input.getAttribute('title'),
                      input.id,
                      input.name,
                      input.getAttribute('data-automation-id')
                    ].map(norm).join(' ');
                    return /m+\s*\/\s*d+\s*\/\s*y+/.test(descriptor) || /\bdate\b/.test(descriptor);
                  });
                return dateLike || null;
              };
              const checkboxCheckedByLabel = (labelText) => {
                const target = norm(labelText);
                for (const label of document.querySelectorAll('label')) {
                  const text = norm(label.textContent);
                  if (!text.includes(target)) {
                    continue;
                  }
                  const forId = label.getAttribute('for');
                  const input = label.control || (forId ? document.getElementById(forId) : null);
                  if (input?.type === 'checkbox' && input.checked) {
                    return true;
                  }
                }
                return false;
              };
              const missing = [];
              const nameInput = findInputByLabel('Name');
              if (!nameInput || !norm(nameInput.value).includes(norm(name))) {
                missing.push('name');
              }
              const dateInput = findDateInput();
              if (!dateInput || !norm(dateInput.value)) {
                missing.push('date');
              }
              if (!checkboxCheckedByLabel(disabilityStatus) && !checkboxCheckedByLabel('No, I do not have a disability')) {
                missing.push('disability status');
              }
              return missing;
            }
            """,
            {"name": profile.applicant_name, "disabilityStatus": profile.disability_status},
        )
    except Exception:
        return ["unable to verify fields"]


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
    if _has_voluntary_disclosure_content(lowered):
        return False
    return "legal equivalent of a signature" in lowered or (
        "review" in lowered and "submit" in lowered and "terms" in lowered
    )


def _has_quick_apply_content(body_text: str) -> bool:
    return (
        "drop file here" in body_text
        or "select files" in body_text
        or "quick apply resume" in body_text
        or "upload either doc" in body_text
    )


def _has_application_questions_content(body_text: str) -> bool:
    return (
        "eligible to work in the united states" in body_text
        or "enrolled in class(es) at asu" in body_text
        or "federal work study" in body_text
        or "federal work-study" in body_text
        or "18 years or older" in body_text
    )


def _has_voluntary_disclosure_content(body_text: str) -> bool:
    return (
        "hispanic or latino" in body_text
        or "ethnicity which most accurately" in body_text
        or "select your gender" in body_text
        or "veteran status" in body_text
    )


def _has_disability_self_id_content(body_text: str) -> bool:
    return (
        "voluntary self-identification of disability" in body_text
        or "cc-305" in body_text
        or "omb control number" in body_text
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
