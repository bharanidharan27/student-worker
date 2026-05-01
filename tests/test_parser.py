from src.scraping.job_detail_parser import parse_job_description


def test_parser_extracts_common_fields_and_sections() -> None:
    raw = """
    Job Title: AI Product Assistant
    Department: EdPlus
    Location: Tempe campus
    Pay Rate: $18.00/hour
    Hours: 10-20 hours

    Minimum Qualifications:
    - Current ASU student
    - Experience with documentation

    Preferred Qualifications:
    - Python
    - QA testing

    Essential Duties:
    - Assist with user stories and product requirements
    - Document AI testing results

    Required Skills:
    - Communication
    - Google Workspace
    """

    parsed = parse_job_description(raw)

    assert parsed.title == "AI Product Assistant"
    assert parsed.department == "EdPlus"
    assert parsed.location == "Tempe campus"
    assert parsed.pay_rate == "$18.00/hour"
    assert parsed.hours == "10-20 hours"
    assert "Current ASU student" in parsed.minimum_qualifications
    assert "Python" in parsed.preferred_qualifications
    assert "Communication" in parsed.required_skills
    assert "Python" in parsed.keywords
    assert "Google Workspace" in parsed.software_tools


def test_parser_handles_missing_fields_gracefully() -> None:
    parsed = parse_job_description("Help students at the front desk and answer email.")

    assert parsed.title is not None
    assert parsed.department is None
    assert parsed.pay_rate is None
    assert isinstance(parsed.minimum_qualifications, list)

