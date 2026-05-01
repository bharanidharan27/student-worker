from src.matching.resume_selector import recommend_resume_for_job, select_resume_type


def test_resume_selector_maps_representative_jobs() -> None:
    assert select_resume_type("Software Developer", "Build Python APIs") == "technical"
    assert select_resume_type("AI Product Assistant", "Write PRDs and user stories") == "product_ai"
    assert select_resume_type("Office Assistant", "Data entry and reception work") == "admin_office"
    assert select_resume_type("Student Ambassador", "Customer service and peer support") == "customer_service"


def test_resume_selector_returns_exact_master_pdf() -> None:
    recommendation = recommend_resume_for_job(
        "Advising Office Aide",
        "Support advising office records, email, phone, data entry, and student communication.",
    )

    assert recommendation.job_family == "office_admin"
    assert recommendation.recommended_resume_type == "admin_office"
    assert recommendation.recommended_resume_name == "Bharanidharan_Maheswaran_WP_Off_Ass.pdf"
    assert recommendation.recommended_resume_path.endswith(
        "Bharanidharan_Maheswaran_WP_Off_Ass.pdf"
    )


def test_non_tech_student_and_hr_jobs_do_not_route_to_technical() -> None:
    admissions = recommend_resume_for_job(
        "Admissions & Recruitment Assistant",
        "Provide student support, outreach, customer service, and recruitment event help.",
    )
    human_resources = recommend_resume_for_job(
        "Human Resources Assistant",
        "Support hiring, onboarding, employee records, payroll, and confidential HR files.",
    )

    assert admissions.job_family == "student_services"
    assert admissions.recommended_resume_type == "customer_service"
    assert human_resources.job_family == "business_hr"
    assert human_resources.recommended_resume_type == "admin_office"


def test_workday_boilerplate_does_not_override_title_family() -> None:
    boilerplate = """
    Equal employment and human resources employee notices. Nursing mothers accommodation.
    Workday product accessibility text.
    """

    digital_culture = recommend_resume_for_job(
        "Digital Culture Summer Institute Assistant",
        boilerplate,
    )
    instructional = recommend_resume_for_job(
        "Instructional Aide: CHM 101 - WEST",
        boilerplate,
    )

    assert digital_culture.job_family == "marketing_media"
    assert instructional.job_family != "product_ai"
    assert instructional.job_family != "business_hr"


def test_business_office_title_routes_to_finance_business_resume() -> None:
    recommendation = recommend_resume_for_job(
        "Business Office Assistant",
        "Support office records, invoices, spreadsheets, email, phone, and documentation.",
    )

    assert recommendation.job_family == "finance_business"
    assert recommendation.recommended_resume_name == "Bharanidharan_M_PartTime_Financial_Off_Aide.pdf"
