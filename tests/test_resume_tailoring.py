from pathlib import Path

from src.eligibility.models import EligibilityAssessment, JobRequirement
from src.eligibility.profile import ApplicantProfile
from src.resume_tailoring import tailor_resume_for_job
from src.storage.db import upsert_job
from src.storage.models import JobRecord


def _write_tex(path: Path, body_lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\newcommand{\resumeItem}[1]{\item #1}",
                r"\newcommand{\resumeItemListStart}{\begin{itemize}}",
                r"\newcommand{\resumeItemListEnd}{\end{itemize}}",
                r"\begin{document}",
                *body_lines,
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_tailor_resume_prefers_latex_source_and_adds_supported_missing_items(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    output_root = tmp_path / "resumes" / "tailored"
    extracted_dir.mkdir(parents=True)
    _write_tex(
        extracted_dir / "Base_Resume" / "main.tex",
        [
            r"\section{Summary}",
            r"\begin{align}",
            r"Python support experience.",
            r"\end{align}",
            r"\section{Technical Skills}",
            r"\begin{itemize}[leftmargin=0.15in, label={}]",
            r"\small{\item{",
            r"\textbf{Tools}{: Git} \\",
            r"\textbf{Platforms}{: Canvas LMS (basic support)}",
            r"}}",
            r"\end{itemize}",
            r"\section{Additional Experience}",
            r"\resumeSubHeadingListStart",
            r"\resumeSubheading{Sales Associate}{2024}{Store}{Remote}",
            r"\resumeItemListStart",
            r"\resumeItem{Helped customers.}",
            r"\resumeItemListEnd",
            r"\section{Availability}",
            r"\resumeItem{-- Available Monâ€“Fri evenings.}",
        ],
    )
    _write_tex(extracted_dir / "Zoom_Resume" / "main.tex", [r"Zoom support and Canvas course shell experience."])
    assessment = EligibilityAssessment(
        status="needs_review",
        summary="Review gaps.",
        requirements=[
            JobRequirement(
                text="Knowledge or experience with Zoom.",
                priority="must",
                category="technology",
                source_quote="Working knowledge of Zoom is required.",
                confidence=0.9,
                match="missing",
            ),
            JobRequirement(
                text="Knowledge or experience with UnsupportedTool.",
                priority="preferred",
                category="technology",
                source_quote="UnsupportedTool preferred.",
                confidence=0.7,
                match="missing",
            ),
        ],
    )
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-tailor",
            title="Technology Consultant",
            raw_description="Support Zoom.",
            recommended_resume_name="Base_Resume.pdf",
            recommended_resume_path="resumes/master/Base_Resume.pdf",
            eligibility_status="needs_review",
            eligibility_json=assessment.model_dump_json(),
        ),
        db_path=db_path,
    )

    result = tailor_resume_for_job(
        job_id,
        db_path=db_path,
        extracted_dir=extracted_dir,
        output_root=output_root,
    )

    output_path = Path(result.output_resume_path)
    assert output_path.exists()
    output_text = output_path.read_text(encoding="utf-8")
    assert output_path.name == "main.tex"
    assert "Targeted Skills" not in output_text
    assert "Experience with Zoom" not in output_text
    assert r"\textbf{Platforms}{: Canvas LMS (basic support)" in output_text
    assert "Zoom" in output_text
    assert "Evidence:" not in output_text
    assert "UnsupportedTool" not in output_text
    assert r"\begin{align}" not in output_text
    assert r"\resumeSubHeadingListEnd" in output_text
    assert r"\resumeItem{-- Available" in output_text
    assert "\\section{Availability}\n  \\resumeItemListStart" in output_text
    notes_text = Path(result.notes_path).read_text(encoding="utf-8")
    assert "Experience with Zoom" in notes_text
    assert "UnsupportedTool" in notes_text


def test_tailor_resume_requires_eligibility_review(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    extracted_dir.mkdir(parents=True)
    _write_tex(extracted_dir / "Base_Resume" / "main.tex", ["Python support experience."])
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-no-review",
            title="Technology Consultant",
            raw_description="Support Zoom.",
            recommended_resume_name="Base_Resume/main.tex",
            recommended_resume_path="resumes/extracted/Base_Resume/main.tex",
        ),
        db_path=db_path,
    )

    try:
        tailor_resume_for_job(job_id, db_path=db_path, extracted_dir=extracted_dir)
    except ValueError as exc:
        assert "eligibility review" in str(exc)
    else:
        raise AssertionError("tailor_resume_for_job should require an eligibility review")


def test_tailor_resume_requires_latex_source_when_docx_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    extracted_dir.mkdir(parents=True)
    (extracted_dir / "Base_Resume.docx").write_text("Python support experience.", encoding="utf-8")
    assessment = EligibilityAssessment(
        status="needs_review",
        summary="Review gaps.",
        requirements=[
            JobRequirement(
                text="Knowledge or experience with Zoom.",
                priority="must",
                category="technology",
                source_quote="Working knowledge of Zoom is required.",
                confidence=0.9,
                match="missing",
            ),
        ],
    )
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-tailor-docx",
            title="Technology Consultant",
            raw_description="Support Zoom.",
            recommended_resume_name="Base_Resume/main.tex",
            recommended_resume_path="resumes/extracted/Base_Resume/main.tex",
            eligibility_status="needs_review",
            eligibility_json=assessment.model_dump_json(),
        ),
        db_path=db_path,
    )

    try:
        tailor_resume_for_job(job_id, db_path=db_path, extracted_dir=extracted_dir)
    except FileNotFoundError as exc:
        assert "LaTeX source" in str(exc)
        assert ".docx" not in str(exc)
    else:
        raise AssertionError("tailor_resume_for_job should require a LaTeX source")


def test_tailor_resume_uses_recommended_latex_main_path(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    output_root = tmp_path / "resumes" / "tailored"
    extracted_dir.mkdir(parents=True)
    _write_tex(
        extracted_dir / "Base_Resume" / "main.tex",
        [
            r"\section{Technical Skills}",
            r"\begin{itemize}[leftmargin=0.15in, label={}]",
            r"\small{\item{",
            r"\textbf{Platforms}{: Canvas}",
            r"}}",
            r"\end{itemize}",
        ],
    )
    assessment = EligibilityAssessment(status="eligible", summary="Ready.", requirements=[])
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-tailor-tex-path",
            title="Technology Consultant",
            raw_description="Support Canvas.",
            recommended_resume_name="Base_Resume/main.tex",
            recommended_resume_path="resumes/extracted/Base_Resume/main.tex",
            eligibility_status="eligible",
            eligibility_json=assessment.model_dump_json(),
        ),
        db_path=db_path,
    )

    result = tailor_resume_for_job(
        job_id,
        db_path=db_path,
        extracted_dir=extracted_dir,
        output_root=output_root,
    )

    assert result.source_resume_path == str(extracted_dir / "Base_Resume" / "main.tex")
    assert result.output_resume_path.endswith("main.tex")


def test_tailor_resume_maps_legacy_pdf_recommendation_to_latex_source(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    output_root = tmp_path / "resumes" / "tailored"
    extracted_dir.mkdir(parents=True)
    _write_tex(
        extracted_dir / "Bharanidharan_M_PartTime_Student_aide" / "main.tex",
        [
            r"\section{Summary}",
            r"\noindent\small{Administrative and student support experience.}",
        ],
    )
    assessment = EligibilityAssessment(status="eligible", summary="Ready.", requirements=[])
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-legacy-resume",
            title="Advising Office Aide",
            raw_description="Support records, email, phone, and office coordination.",
            recommended_resume_name="Bharanidharan_Maheswaran_WP_Off_Ass.pdf",
            recommended_resume_path="resumes/master/Bharanidharan_Maheswaran_WP_Off_Ass.pdf",
            eligibility_status="eligible",
            eligibility_json=assessment.model_dump_json(),
        ),
        db_path=db_path,
    )

    result = tailor_resume_for_job(
        job_id,
        db_path=db_path,
        extracted_dir=extracted_dir,
        output_root=output_root,
    )

    assert result.source_resume_path == str(
        extracted_dir / "Bharanidharan_M_PartTime_Student_aide" / "main.tex"
    )


def test_tailor_resume_merges_availability_and_portfolio_without_new_latex_section(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    extracted_dir = tmp_path / "resumes" / "extracted"
    output_root = tmp_path / "resumes" / "tailored"
    extracted_dir.mkdir(parents=True)
    _write_tex(
        extracted_dir / "Base_Resume" / "main.tex",
        [
            r"\section{Summary}",
            r"\noindent\small{Technology-focused graduate student.}",
            r"\section{Technical Skills}",
            r"\begin{itemize}[leftmargin=0.15in, label={}]",
            r"\small{\item{",
            r"\textbf{Tools}{: Git}",
            r"}}",
            r"\end{itemize}",
            r"\section{Availability}",
            r"\resumeItem{-- Available evenings.}",
        ],
    )
    assessment = EligibilityAssessment(
        status="needs_review",
        summary="Review gaps.",
        requirements=[
            JobRequirement(
                text="Must be available for 20 hours per week.",
                priority="must",
                category="availability",
                source_quote="Must be available for 20 hours per week.",
                confidence=0.9,
                match="missing",
            ),
            JobRequirement(
                text="Portfolio may be required.",
                priority="preferred",
                category="portfolio",
                source_quote="Strong portfolio demonstrating work preferred.",
                confidence=0.8,
                match="missing",
            ),
        ],
    )
    job_id = upsert_job(
        JobRecord(
            workday_id="JR-tailor-categories",
            title="Portfolio Support Aide",
            raw_description="Portfolio and availability role.",
            recommended_resume_name="Base_Resume.pdf",
            recommended_resume_path="resumes/master/Base_Resume.pdf",
            eligibility_status="needs_review",
            eligibility_json=assessment.model_dump_json(),
        ),
        db_path=db_path,
    )
    profile = ApplicantProfile(
        available_hours_per_week=20,
        portfolio_links=["https://portfolio.example"],
    )

    result = tailor_resume_for_job(
        job_id,
        db_path=db_path,
        extracted_dir=extracted_dir,
        output_root=output_root,
        profile=profile,
    )

    output_text = Path(result.output_resume_path).read_text(encoding="utf-8")
    notes_text = Path(result.notes_path).read_text(encoding="utf-8")
    assert "Targeted Skills" not in output_text
    assert "Portfolio available upon request" in output_text
    assert "Available up to 20 hours/week" in output_text
    assert "Portfolio available upon request" in notes_text
    assert "Available up to 20 hours/week" in notes_text
