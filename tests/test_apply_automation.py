from pathlib import Path

from src.apply_automation import (
    ApplicationProfile,
    AutoApplyResult,
    MANUAL_ADVANCE_SECTIONS,
    _count_uploaded_resume_mentions,
    _current_section_label,
    _extract_applied_marker,
    _fill_known_section,
    _looks_like_later_step,
    _looks_like_review_page,
    _resume_already_uploaded,
    _upload_resume,
    _wait_for_user_to_advance,
    auto_apply_job,
    auto_apply_queue,
)
from src.storage.db import get_connection, upsert_job
from src.storage.models import JobRecord


def _job(
    workday_id: str,
    title: str,
    resume_path: Path,
    fit_score: int = 90,
    fit_label: str = "Strong Fit",
    status: str = "new",
) -> JobRecord:
    return JobRecord(
        workday_id=workday_id,
        title=title,
        location="Tempe campus",
        posting_date="04/30/2026",
        url=f"https://www.myworkday.com/asu/job/{workday_id}",
        raw_description=f"{title} role.",
        fit_score=fit_score,
        fit_label=fit_label,
        job_family="office_admin",
        recommended_resume_type="admin_office",
        recommended_resume_name=resume_path.name,
        recommended_resume_path=str(resume_path),
        status=status,
    )


def test_auto_apply_blocks_missing_resume_and_marks_reviewing(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    job_id = upsert_job(_job("JR-missing", "Office Aide", tmp_path / "missing.pdf"), db_path)

    result = auto_apply_job(job_id, db_path=db_path, auth_state_path=auth_path)

    assert result.ok is False
    assert result.needs_review is True
    assert "does not exist" in result.message
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT status, application_notes FROM jobs WHERE id = ?;", (job_id,)).fetchone()
    assert row["status"] == "reviewing"
    assert "Auto apply blocked" in row["application_notes"]


def test_auto_apply_uses_driver_and_marks_applied_on_submit(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    resume_path = tmp_path / "resume.pdf"
    resume_path.write_text("resume", encoding="utf-8")
    job_id = upsert_job(_job("JR-submit", "Office Aide", resume_path), db_path)

    def driver(job, submit, headed, debug_dump_dir, auth_state_path, timeout_ms, profile):
        assert job.id == job_id
        assert submit is True
        assert headed is False
        assert job.resume_path == resume_path
        assert profile.applicant_name == "Bharanidharan Maheswaran"
        return AutoApplyResult(job.id, True, True, False, "Application submitted by test driver.")

    result = auto_apply_job(
        job_id,
        db_path=db_path,
        auth_state_path=auth_path,
        submit=True,
        headed=False,
        driver=driver,
    )

    assert result.submitted is True
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT status, application_notes, applied_at FROM jobs WHERE id = ?;", (job_id,)).fetchone()
    assert row["status"] == "applied"
    assert row["application_notes"] == "Application submitted by test driver."
    assert row["applied_at"]


def test_auto_apply_queue_filters_to_strong_fit_min_score(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    resume_path = tmp_path / "resume.pdf"
    resume_path.write_text("resume", encoding="utf-8")
    upsert_job(_job("JR-strong", "Strong Office Aide", resume_path, 90, "Strong Fit"), db_path)
    upsert_job(_job("JR-low", "Low Office Aide", resume_path, 70, "Strong Fit"), db_path)
    upsert_job(_job("JR-possible", "Possible Office Aide", resume_path, 95, "Possible Fit"), db_path)

    called: list[int] = []

    def driver(job, submit, headed, debug_dump_dir, auth_state_path, timeout_ms, profile):
        called.append(job.id)
        return AutoApplyResult(job.id, True, False, True, "Resume uploaded by test driver.")

    results = auto_apply_queue(
        db_path=db_path,
        auth_state_path=auth_path,
        limit=10,
        min_score=80,
        fit_label="Strong Fit",
        driver=driver,
    )

    assert len(results) == 1
    assert len(called) == 1
    assert results[0].needs_review is True


def test_application_profile_defaults_match_known_fixed_answers() -> None:
    profile = ApplicationProfile()

    assert profile.work_authorization == "Yes"
    assert profile.enrolled_at_asu == "Yes"
    assert profile.federal_work_study == "No"
    assert profile.age_18_or_older == "Yes"
    assert profile.hispanic_or_latino == "No"
    assert profile.disability_status.startswith("No, I do not have a disability")


def test_review_detection_does_not_match_sidebar_only_text() -> None:
    quick_apply_text = "Quick Apply My Experience Application Questions Review Your job application will be saved."
    review_text = "Review I understand that checking this box is the legal equivalent of a signature accepting the terms above."

    assert _looks_like_review_page(quick_apply_text) is False
    assert _looks_like_review_page(review_text) is True


def test_extract_applied_marker_detects_existing_workday_application() -> None:
    text = "Tech Devil Consultant Applied 04/30/2026, 1:08 PM Job Details"

    assert _extract_applied_marker(text) == "Applied 04/30/2026, 1:08 PM"


def test_extract_applied_marker_accepts_lowercase_ui_text() -> None:
    text = "Tech Devil Consultant applied 04/30/2026, 1:08 PM Job Details"

    assert _extract_applied_marker(text) == "applied 04/30/2026, 1:08 PM"


def test_resume_already_uploaded_detects_file_name(tmp_path: Path) -> None:
    resume_path = tmp_path / "Bharanidharan_Maheswaran_Resume.pdf"

    class FakePage:
        def locator(self, selector):
            class Body:
                def inner_text(self, timeout):
                    return "Uploaded file Bharanidharan_Maheswaran_Resume.pdf"

            return Body()

    assert _resume_already_uploaded(FakePage(), resume_path) is True


def test_my_experience_is_a_manual_advance_section() -> None:
    assert "my experience" in MANUAL_ADVANCE_SECTIONS


def test_current_section_label_recognises_workday_steps() -> None:
    assert _current_section_label("Quick Apply please upload") == "quick apply"
    assert _current_section_label("My Experience Work Experience") == "my experience"
    assert (
        _current_section_label("Application Questions Are you eligible")
        == "application questions"
    )
    assert _current_section_label("Random unrelated text") is None


def test_wait_for_user_to_advance_returns_true_when_section_changes() -> None:
    sequence = [
        "My Experience Job Title Company",
        "My Experience Job Title Company",
        "Application Questions Are you eligible",
    ]
    index = {"value": 0}

    class FakePage:
        def wait_for_timeout(self, ms: int) -> None:
            return None

        def locator(self, selector: str):
            text = sequence[min(index["value"], len(sequence) - 1)]
            index["value"] += 1

            class Body:
                def inner_text(self, timeout: int) -> str:
                    return text

            return Body()

    advanced = _wait_for_user_to_advance(
        FakePage(), "my experience", timeout_ms=5_000, poll_ms=100
    )
    assert advanced is True


def test_wait_for_user_to_advance_times_out_when_section_stays() -> None:
    class FakePage:
        def wait_for_timeout(self, ms: int) -> None:
            return None

        def locator(self, selector: str):
            class Body:
                def inner_text(self, timeout: int) -> str:
                    return "My Experience Work Experience Job Title"

            return Body()

    advanced = _wait_for_user_to_advance(
        FakePage(), "my experience", timeout_ms=300, poll_ms=100
    )
    assert advanced is False


def test_looks_like_later_step_disambiguates_sidebar_quick_apply() -> None:
    sidebar_only = (
        "quick apply my experience application questions voluntary"
        " disclosures self identify review"
    )
    quick_apply_step = "quick apply please read drop file here select files"

    assert _looks_like_later_step(sidebar_only) is True
    assert _looks_like_later_step(quick_apply_step) is False


def _job_with_resume(resume_path: Path):
    """Build a minimal AutoApplyJob-shaped object for section tests."""
    from src.apply_automation import AutoApplyJob

    return AutoApplyJob(
        id=1,
        workday_id="JR-test",
        title="Office Aide",
        url="https://example/job/JR-test",
        resume_path=resume_path,
        fit_score=90,
        fit_label="Strong Fit",
    )


class _ResumeUploadFakePage:
    """Test double that records every Workday locator + click interaction.

    The body text is configurable per scenario so we can cover the Quick
    Apply step (resume needs uploading), the Quick Apply step after the
    upload succeeded (resume already mentioned once), and the My
    Experience step (Quick Apply word still in sidebar but page must be
    treated as a later step).
    """

    def __init__(self, body_text: str, file_input_count: int = 1):
        self._body_text = body_text
        self._file_input_count = file_input_count
        self.set_input_files_calls: list[str] = []
        self.remove_button_clicks = 0

        outer = self

        class FileInput:
            def count(self) -> int:
                return outer._file_input_count

            def set_input_files(self, path: str, timeout: int) -> None:
                outer.set_input_files_calls.append(path)

        class Body:
            def inner_text(self, timeout: int) -> str:
                return outer._body_text

            @property
            def first(self):
                return self

        class RemoveButton:
            def click(self, timeout: int) -> None:
                outer.remove_button_clicks += 1

        self._file_input = FileInput()
        self._body = Body()
        self._remove_button = RemoveButton()

    def locator(self, selector: str):
        if selector == "input[type='file']":
            class _Wrapper:
                def __init__(self, inner):
                    self._inner = inner
                    self.first = inner

                def count(self):
                    return self._inner.count()

            return _Wrapper(self._file_input)
        if selector == "body":
            return self._body
        # any other locator returns an empty stub
        class Empty:
            first = property(lambda self: self)

            def count(self):
                return 0

            def click(self, timeout):
                return None

        return Empty()

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def get_by_role(self, role, name=None):
        # Return an object that simulates a Remove button being click-able.
        # We never want this called from My Experience because that's the bug.
        return self._remove_button


def test_my_experience_does_not_re_upload_or_remove(tmp_path: Path) -> None:
    """Regression: on My Experience, the tool must not touch the resume.

    Previously the section detector matched 'quick apply' in the left
    sidebar text and re-entered the upload path. _upload_resume then saw
    the resume mentioned twice (Resume/CV subsection + sidebar) and
    indiscriminately clicked every Remove button on the page, deleting
    every auto-filled Work Experience and Education card.
    """
    resume_path = tmp_path / "Bharanidharan_Resume.pdf"
    resume_path.write_text("resume", encoding="utf-8")

    body_text = (
        "Quick Apply My Experience Application Questions Voluntary Disclosures"
        " Self Identify Review Source Current Worker Work Experience Job Title"
        " Member Technical Staff Company Zoho Corporation Education Arizona"
        " State University Resume/CV and Cover Letter Bharanidharan_Resume.pdf"
        " Upload"
    )
    page = _ResumeUploadFakePage(body_text=body_text)
    job = _job_with_resume(resume_path)
    profile = ApplicationProfile()

    result = _fill_known_section(page, job, profile, timeout_ms=1_000)

    assert result.ok is True
    assert page.set_input_files_calls == [], "resume must not be re-uploaded on My Experience"
    assert page.remove_button_clicks == 0, "Remove buttons must not be clicked on My Experience"


def test_quick_apply_step_uploads_only_when_no_existing_resume(tmp_path: Path) -> None:
    resume_path = tmp_path / "Bharanidharan_Resume.pdf"
    resume_path.write_text("resume", encoding="utf-8")

    # Quick Apply step, body does NOT contain a later-step marker, and the
    # resume has not been attached yet — the file input should be set.
    page = _ResumeUploadFakePage(
        body_text="Quick Apply Please read Drop file here Select files"
    )
    assert _upload_resume(page, resume_path, timeout_ms=1_000) is True
    assert page.set_input_files_calls == [str(resume_path)]
    assert page.remove_button_clicks == 0


def test_upload_resume_skips_when_already_attached(tmp_path: Path) -> None:
    resume_path = tmp_path / "Bharanidharan_Resume.pdf"
    resume_path.write_text("resume", encoding="utf-8")

    # File mentioned twice (e.g. Quick Apply preview + listing). The previous
    # implementation clicked Remove buttons in this case and wiped data.
    page = _ResumeUploadFakePage(
        body_text=(
            "Quick Apply Bharanidharan_Resume.pdf preview"
            " Bharanidharan_Resume.pdf attached"
        )
    )

    assert _upload_resume(page, resume_path, timeout_ms=1_000) is True
    assert page.set_input_files_calls == []
    assert page.remove_button_clicks == 0


def test_count_uploaded_resume_mentions_detects_duplicates(tmp_path: Path) -> None:
    resume_path = tmp_path / "Bharanidharan_Maheswaran_Resume.pdf"

    class FakePage:
        def locator(self, selector):
            class Body:
                def inner_text(self, timeout):
                    return (
                        "Bharanidharan_Maheswaran_Resume.pdf "
                        "Bharanidharan_Maheswaran_Resume.pdf"
                    )

            return Body()

    assert _count_uploaded_resume_mentions(FakePage(), resume_path) == 2
