"""Curated LaTeX resume catalog used for local recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.storage.models import ResumeType


EXTRACTED_RESUME_DIR = Path("resumes/extracted")

LEGACY_RESUME_SOURCE_ALIASES = {
    "Bharanidharan_Maheswaran_WP_Off_Ass": "Bharanidharan_M_PartTime_Student_aide",
    "Bharanidharan_Maheswaran_Op_Sup": "Bharanidharan_M_PartTime_Op_Support_Ass",
    "Bharanidharan_Maheswaran_Resume_WP_Carey": "Bharanidharan_M_PartTime_Op_Support_Ass",
    "Bharanidharan_Maheswaran_Resume_Cronkite": "Bharanidharan_M_PartTime_NonTech_Cronkite",
    "Bharanidharan_Maheswaran_Technical_Proj_Ass": "Bharanidharan_M_PartTime_Tech_Proj_Ass",
    "Bharanidharan_Maheswaran_SBS_Clerk": "Bharanidharan_M_PartTime_Student_aide",
    "Bharanidharan_M_PartTime_NonTech": "Bharanidharan_M_PartTime_Student_aide",
    "Bharanidharan_M_PartTime_NonTech_NoDets": "Bharanidharan_M_PartTime_Student_aide",
    "Bharanidharan_M_PartTime_Resume": "Bharanidharan_M_PartTime_AI_Product_Ass",
}


@dataclass(frozen=True)
class ResumeCatalogEntry:
    job_family: str
    resume_type: ResumeType
    resume_name: str
    resume_path: str


def _entry(job_family: str, resume_type: ResumeType, source_dir: str) -> ResumeCatalogEntry:
    return ResumeCatalogEntry(
        job_family=job_family,
        resume_type=resume_type,
        resume_name=f"{source_dir}/main.tex",
        resume_path=(EXTRACTED_RESUME_DIR / source_dir / "main.tex").as_posix(),
    )


RESUME_CATALOG: dict[str, ResumeCatalogEntry] = {
    "office_admin": _entry(
        "office_admin",
        "admin_office",
        "Bharanidharan_M_PartTime_Student_aide",
    ),
    "front_desk": _entry(
        "front_desk",
        "customer_service",
        "Bharanidharan_M_Front_Desk_Sch_Pol",
    ),
    "operations_support": _entry(
        "operations_support",
        "customer_service",
        "Bharanidharan_M_PartTime_Op_Support_Ass",
    ),
    "student_services": _entry(
        "student_services",
        "customer_service",
        "Bharanidharan_M_PartTime_Student_aide",
    ),
    "finance_business": _entry(
        "finance_business",
        "admin_office",
        "Bharanidharan_M_PartTime_Financial_Off_Aide",
    ),
    "business_hr": _entry(
        "business_hr",
        "admin_office",
        "Bharanidharan_M_PartTime_Op_Support_Ass",
    ),
    "marketing_media": _entry(
        "marketing_media",
        "customer_service",
        "Bharanidharan_M_PartTime_Marketing_Spec_",
    ),
    "journalism_media": _entry(
        "journalism_media",
        "customer_service",
        "Bharanidharan_M_PartTime_NonTech_Cronkite",
    ),
    "research_lab": _entry(
        "research_lab",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Proj_Ass",
    ),
    "technical_assistant": _entry(
        "technical_assistant",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass",
    ),
    "zoom_it": _entry(
        "zoom_it",
        "technical",
        "Bharanidharan_M_Zoom_Engineer",
    ),
    "general_tech": _entry(
        "general_tech",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass",
    ),
    "product_ai": _entry(
        "product_ai",
        "product_ai",
        "Bharanidharan_M_PartTime_AI_Product_Ass",
    ),
    "data_tech": _entry(
        "data_tech",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass",
    ),
    "card_services": _entry(
        "card_services",
        "customer_service",
        "Bharanidharan_M_PartTime_Sun_Devil_Card_Aide",
    ),
    "clerical_sbs": _entry(
        "clerical_sbs",
        "admin_office",
        "Bharanidharan_M_PartTime_Student_aide",
    ),
    "studio_support": _entry(
        "studio_support",
        "customer_service",
        "Bharanidharan_Studio_Associate",
    ),
    "general_nontech": _entry(
        "general_nontech",
        "admin_office",
        "Bharanidharan_M_PartTime_Student_aide",
    ),
    "music_performance": _entry(
        "music_performance",
        "customer_service",
        "Bharanidharan_M_PartTime_NonTech_Cronkite",
    ),
}


def catalog_entry_for_family(job_family: str | None) -> ResumeCatalogEntry:
    if job_family and job_family in RESUME_CATALOG:
        return RESUME_CATALOG[job_family]
    return RESUME_CATALOG["general_nontech"]
