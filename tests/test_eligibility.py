from types import SimpleNamespace

from src.eligibility.assessor import assess_job_eligibility
from src.eligibility.profile import ApplicantProfile
from src.scraping.job_detail_parser import parse_job_description


class OfflineClient:
    available = False


class FailingClient:
    available = True
    config = SimpleNamespace(provider="test-provider", model="test-model")

    def chat_json(self, **kwargs):
        raise TimeoutError("read operation timed out")


def _assess(raw: str, profile: ApplicantProfile | None = None, llm_client=None):
    return assess_job_eligibility(
        parse_job_description(raw),
        raw,
        profile=profile or ApplicantProfile(),
        llm_client=llm_client or OfflineClient(),
    )


def test_undergraduate_only_role_is_ineligible_for_masters_profile() -> None:
    raw = """
    Job Title: Undergraduate Peer Mentor
    Minimum Qualifications: Must be an undergraduate student currently enrolled at ASU.
    Responsibilities: Support students and document meetings.
    """

    assessment = _assess(raw, ApplicantProfile(degree_level="masters"))

    assert assessment.status == "ineligible"
    assert any("undergraduate" in blocker.lower() for blocker in assessment.blockers)
    assert any(action.action_type == "do_not_apply" for action in assessment.non_resume_actions)


def test_required_hours_with_unknown_availability_needs_review() -> None:
    raw = """
    Job Title: Office Aide
    Minimum Qualifications: Must be available to work 20 hours per week.
    Responsibilities: Support office records and email.
    """

    assessment = _assess(raw, ApplicantProfile(available_hours_per_week=None))

    assert assessment.status == "needs_review"
    assert any(action.action_type == "confirm_availability" for action in assessment.non_resume_actions)


def test_federal_work_study_role_is_ineligible_when_profile_says_no() -> None:
    raw = """
    Job Title: Office Aide
    Minimum Qualifications: Federal Work-Study eligibility is required.
    Responsibilities: Support office records and email.
    """

    assessment = _assess(raw, ApplicantProfile(federal_work_study=False))

    assert assessment.status == "ineligible"
    assert any("work-study" in blocker.lower() for blocker in assessment.blockers)
    assert any(action.action_type == "do_not_apply" for action in assessment.non_resume_actions)


def test_present_technology_missing_from_resume_gets_resume_suggestion() -> None:
    raw = """
    Job Title: Data Assistant
    Minimum Qualifications: Experience with SQL required.
    Responsibilities: Maintain data quality reports.
    """
    profile = ApplicantProfile(technologies=["SQL"], resume_keywords=["Python"])

    assessment = _assess(raw, profile)

    assert assessment.status in {"eligible", "needs_review"}
    assert any("SQL" in item.requirement for item in assessment.resume_suggestions)


def test_empty_resume_keywords_do_not_create_local_resume_suggestions() -> None:
    raw = """
    Job Title: Data Assistant
    Minimum Qualifications: Experience with SQL required.
    Responsibilities: Maintain data quality reports.
    """
    profile = ApplicantProfile(technologies=["SQL"], resume_keywords=[])

    assessment = _assess(raw, profile)

    assert assessment.status in {"eligible", "needs_review"}
    assert assessment.resume_suggestions == []


def test_preferred_office_skill_does_not_create_resume_suggestion() -> None:
    raw = """
    Job Title: Office Aide
    Minimum Qualifications: Current ASU student.
    Preferred Qualifications: Experience with Excel.
    Responsibilities: Support office records and email.
    """
    profile = ApplicantProfile(technologies=["Excel"], resume_keywords=["Python"])

    assessment = _assess(raw, profile)

    assert assessment.status == "eligible"
    assert assessment.resume_suggestions == []


def test_llm_failure_uses_local_rules_without_user_warning() -> None:
    raw = """
    Job Title: Office Aide
    Minimum Qualifications: Current ASU student.
    Responsibilities: Support office records and email.
    """

    assessment = _assess(raw, llm_client=FailingClient())

    assert assessment.llm_used is False
    assert assessment.provider == "test-provider"
    assert assessment.model == "test-model"
    assert not any("llm" in warning.lower() for warning in assessment.warnings)


def test_preferred_missing_skill_is_not_a_warning_or_blocker() -> None:
    raw = """
    Job Title: Office Aide
    Minimum Qualifications: Current ASU student.
    Preferred Qualifications: Experience with Tableau.
    Responsibilities: Support office records and email.
    """
    profile = ApplicantProfile(technologies=["Python"])

    assessment = _assess(raw, profile)

    assert assessment.status == "eligible"
    assert assessment.blockers == []
    assert assessment.warnings == []
    assert any(
        requirement.priority == "preferred" and requirement.match == "missing"
        for requirement in assessment.requirements
    )


def test_detail_oriented_is_not_tagged_as_technology_from_ai_substring() -> None:
    assessment = _assess(
        """
        Job Title: Office Aide
        Minimum Qualifications: Current ASU student.
        Preferred Qualifications: Detail-oriented.
        """
    )

    detail_requirement = next(
        requirement for requirement in assessment.requirements if "detail-oriented" in requirement.text.lower()
    )
    assert detail_requirement.category == "other"


def test_no_api_key_still_returns_local_assessment() -> None:
    assessment = _assess("Minimum Qualifications: Current ASU student.")

    assert assessment.llm_used is False
    assert assessment.status in {"eligible", "needs_review", "ineligible"}
