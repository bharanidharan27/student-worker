"""Curated master resume catalog used for local recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.storage.models import ResumeType


MASTER_RESUME_DIR = Path("resumes/master")


@dataclass(frozen=True)
class ResumeCatalogEntry:
    job_family: str
    resume_type: ResumeType
    resume_name: str
    resume_path: str


def _entry(job_family: str, resume_type: ResumeType, filename: str) -> ResumeCatalogEntry:
    return ResumeCatalogEntry(
        job_family=job_family,
        resume_type=resume_type,
        resume_name=filename,
        resume_path=str(MASTER_RESUME_DIR / filename),
    )


RESUME_CATALOG: dict[str, ResumeCatalogEntry] = {
    "office_admin": _entry(
        "office_admin",
        "admin_office",
        "Bharanidharan_Maheswaran_WP_Off_Ass.pdf",
    ),
    "front_desk": _entry(
        "front_desk",
        "customer_service",
        "Bharanidharan_M_Front_Desk_Sch_Pol.pdf",
    ),
    "operations_support": _entry(
        "operations_support",
        "customer_service",
        "Bharanidharan_Maheswaran_Op_Sup.pdf",
    ),
    "student_services": _entry(
        "student_services",
        "customer_service",
        "Bharanidharan_M_PartTime_Student_aide.pdf",
    ),
    "finance_business": _entry(
        "finance_business",
        "admin_office",
        "Bharanidharan_M_PartTime_Financial_Off_Aide.pdf",
    ),
    "business_hr": _entry(
        "business_hr",
        "admin_office",
        "Bharanidharan_Maheswaran_Resume_WP_Carey.pdf",
    ),
    "marketing_media": _entry(
        "marketing_media",
        "customer_service",
        "Bharanidharan_M_PartTime_Marketing_Spec_.pdf",
    ),
    "journalism_media": _entry(
        "journalism_media",
        "customer_service",
        "Bharanidharan_Maheswaran_Resume_Cronkite.pdf",
    ),
    "research_lab": _entry(
        "research_lab",
        "technical",
        "Bharanidharan_Maheswaran_Technical_Proj_Ass.pdf",
    ),
    "technical_assistant": _entry(
        "technical_assistant",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass.pdf",
    ),
    "zoom_it": _entry(
        "zoom_it",
        "technical",
        "Bharanidharan_M_Zoom_Engineer.pdf",
    ),
    "general_tech": _entry(
        "general_tech",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass.pdf",
    ),
    "product_ai": _entry(
        "product_ai",
        "product_ai",
        "Bharanidharan_M_PartTime_Resume.pdf",
    ),
    "data_tech": _entry(
        "data_tech",
        "technical",
        "Bharanidharan_M_PartTime_Tech_Ass.pdf",
    ),
    "card_services": _entry(
        "card_services",
        "customer_service",
        "Bharanidharan_M_PartTime_Sun_Devil_Card_Aide.pdf",
    ),
    "clerical_sbs": _entry(
        "clerical_sbs",
        "admin_office",
        "Bharanidharan_Maheswaran_SBS_Clerk.pdf",
    ),
    "studio_support": _entry(
        "studio_support",
        "customer_service",
        "Bharanidharan_Studio_Associate.pdf",
    ),
    "general_nontech": _entry(
        "general_nontech",
        "admin_office",
        "Bharanidharan_M_PartTime_NonTech.pdf",
    ),
    "music_performance": _entry(
        "music_performance",
        "customer_service",
        "Bharanidharan_M_PartTime_NonTech.pdf",
    ),
}


def catalog_entry_for_family(job_family: str | None) -> ResumeCatalogEntry:
    if job_family and job_family in RESUME_CATALOG:
        return RESUME_CATALOG[job_family]
    return RESUME_CATALOG["general_nontech"]
