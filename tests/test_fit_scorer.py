from pathlib import Path

from src.matching.fit_scorer import rescore_db, score_fit
from src.scraping.job_detail_parser import parse_job_description
from src.storage.db import get_connection, upsert_job
from src.storage.models import JobRecord


def _fit(raw: str):
    return score_fit(parse_job_description(raw), raw)


def test_scorer_produces_strong_fit() -> None:
    raw = """
    Job Title: Software Developer Assistant
    Location: ASU Tempe campus
    Hours: 10-20 hours
    Minimum Qualifications: Current Computer Science graduate student.
    Responsibilities: Develop Python APIs, write SQL queries, automate backend workflows,
    document technical work, and communicate with the team.
    """
    fit = _fit(raw)

    assert fit.label == "Strong Fit"
    assert fit.score >= 80
    assert fit.recommended_resume_type == "technical"
    assert fit.recommended_resume_name == "Bharanidharan_M_PartTime_Tech_Ass.pdf"


def test_advising_office_aide_routes_to_admin_office_resume() -> None:
    raw = """
    Job Title: Advising Office Aide
    Location: ASU Tempe campus
    Minimum Qualifications: Current ASU student.
    Responsibilities: Support the advising office with student records, data entry,
    Microsoft Office documents, scheduling, email, phone communication, and confidential files.
    """
    fit = _fit(raw)

    assert fit.label == "Strong Fit"
    assert fit.score >= 80
    assert fit.job_family == "office_admin"
    assert fit.recommended_resume_type == "admin_office"
    assert fit.recommended_resume_name == "Bharanidharan_Maheswaran_WP_Off_Ass.pdf"


def test_workday_boilerplate_does_not_cap_normal_office_role() -> None:
    raw = """
    Job Title: Advising Office Aide
    Location: ASU Tempe campus
    Minimum Qualifications: Current ASU student.
    Responsibilities: Support the advising office with student records, data entry,
    Microsoft Office documents, scheduling, email, phone communication, and confidential files.

    Equal employment and human resources employee notices.
    Nursing mothers accommodation text from Workday.
    """
    fit = _fit(raw)

    assert fit.label == "Strong Fit"
    assert fit.score >= 80
    assert fit.job_family == "office_admin"


def test_ai_product_assistant_routes_to_product_ai_resume() -> None:
    raw = """
    Job Title: AI Product Assistant
    Location: Hybrid
    Minimum Qualifications: Current ASU student.
    Responsibilities: Write product requirements, user stories, support AI testing,
    document QA results, analyze product feedback, and communicate with stakeholders.
    """
    fit = _fit(raw)

    assert fit.label == "Strong Fit"
    assert fit.score >= 80
    assert fit.job_family == "product_ai"
    assert fit.recommended_resume_type == "product_ai"
    assert fit.recommended_resume_name == "Bharanidharan_M_PartTime_Resume.pdf"


def test_admissions_recruitment_uses_student_services_resume_not_technical() -> None:
    raw = """
    Job Title: Admissions & Recruitment Assistant
    Location: Downtown Phoenix campus
    Minimum Qualifications: Current ASU student.
    Responsibilities: Provide student support, answer admissions questions, assist
    recruitment events, coordinate outreach, and deliver customer service by phone and email.
    """
    fit = _fit(raw)

    assert fit.score >= 60
    assert fit.job_family == "student_services"
    assert fit.recommended_resume_type == "customer_service"
    assert fit.recommended_resume_type != "technical"


def test_human_resources_assistant_uses_business_admin_resume_not_technical() -> None:
    raw = """
    Job Title: Human Resources Assistant
    Location: Downtown Phoenix campus
    Responsibilities: Support hiring, onboarding, employee records, payroll files,
    Microsoft Office tracking, confidential documentation, email, and phone communication.
    """
    fit = _fit(raw)

    assert fit.score >= 60
    assert fit.job_family == "business_hr"
    assert fit.recommended_resume_type == "admin_office"
    assert fit.recommended_resume_type != "technical"


def test_data_aide_routes_to_data_or_technical_resume() -> None:
    raw = """
    Job Title: Data Aide - DPC
    Location: Downtown Phoenix campus
    Minimum Qualifications: Current ASU student.
    Responsibilities: Maintain database records, prepare reporting dashboards,
    use SQL and Excel, document data quality issues, and communicate with the team.
    """
    fit = _fit(raw)

    assert fit.score >= 60
    assert fit.job_family == "data_tech"
    assert fit.recommended_resume_type == "technical"
    assert fit.recommended_resume_name == "Bharanidharan_M_PartTime_Tech_Ass.pdf"


def test_string_quartet_performer_is_low_fit_and_not_technical() -> None:
    raw = """
    Job Title: String Quartet Performer
    Location: ASU Tempe campus
    Responsibilities: Perform music as part of a quartet for campus concerts and events.
    """
    fit = _fit(raw)

    assert fit.label == "Skip"
    assert fit.score < 60
    assert fit.job_family == "music_performance"
    assert fit.recommended_resume_type != "technical"


def test_scorer_caps_certification_only_roles() -> None:
    raw = """
    Job Title: Lifeguard
    Responsibilities: Maintain pool safety and enforce aquatic facility rules.
    Certification required.
    """
    fit = _fit(raw)

    assert fit.label == "Skip"
    assert fit.score < 60


def test_rescore_db_updates_existing_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    raw = """
    Job Title: Advising Office Aide
    Location: ASU Tempe campus
    Responsibilities: Support office records, data entry, Microsoft Office documents,
    student communication, email, phone, and confidential files.
    """
    upsert_job(
        JobRecord(
            workday_id="JR120138",
            title="Advising Office Aide",
            location="Tempe campus",
            raw_description=raw,
        ),
        db_path,
    )

    assert rescore_db(db_path) == 1

    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT fit_label, job_family, recommended_resume_type, recommended_resume_name
            FROM jobs
            WHERE workday_id = 'JR120138';
            """
        ).fetchone()

    assert row["fit_label"] == "Strong Fit"
    assert row["job_family"] == "office_admin"
    assert row["recommended_resume_type"] == "admin_office"
    assert row["recommended_resume_name"] == "Bharanidharan_Maheswaran_WP_Off_Ass.pdf"
