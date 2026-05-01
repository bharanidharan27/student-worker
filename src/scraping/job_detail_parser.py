"""Rule-based parser for pasted ASU student job descriptions."""

from __future__ import annotations

import re

from src.matching.keyword_extractor import extract_keywords, extract_software_tools
from src.storage.models import ParsedJob
from src.utils.text_cleaner import clean_list_item, normalize_whitespace, sentence_split


FIELD_PATTERNS: dict[str, list[str]] = {
    "title": [
        r"^(?:job\s+)?title\s*[:\-]\s*(?P<value>.+)$",
        r"^position\s*[:\-]\s*(?P<value>.+)$",
    ],
    "department": [
        r"^department\s*[:\-]\s*(?P<value>.+)$",
        r"^hiring\s+unit\s*[:\-]\s*(?P<value>.+)$",
    ],
    "pay_rate": [
        r"^(?:pay|pay rate|hourly rate|compensation)\s*[:\-]\s*(?P<value>.+)$",
        r"(?P<value>\$\s?\d+(?:\.\d{2})?\s*(?:\/|per)?\s*(?:hour|hr)?)",
    ],
    "hours": [
        r"^(?:hours|schedule)\s*[:\-]\s*(?P<value>.+)$",
        r"(?P<value>\b\d{1,2}\s*(?:-|to)\s*\d{1,2}\s+hours?\b)",
    ],
    "location": [
        r"^location\s*[:\-]\s*(?P<value>.+)$",
        r"^campus\s*[:\-]\s*(?P<value>.+)$",
    ],
}

SECTION_PATTERNS: dict[str, list[str]] = {
    "minimum_qualifications": [
        r"minimum qualifications?",
        r"required qualifications?",
        r"basic qualifications?",
    ],
    "preferred_qualifications": [
        r"preferred qualifications?",
        r"desired qualifications?",
    ],
    "essential_duties": [
        r"essential duties",
        r"job duties",
        r"responsibilities",
        r"duties and responsibilities",
    ],
    "required_skills": [
        r"required skills?",
        r"skills",
        r"knowledge, skills",
    ],
}


def parse_job_description(raw_description: str) -> ParsedJob:
    text = normalize_whitespace(raw_description)
    lines = [line.strip() for line in text.splitlines()]
    nonempty_lines = [line for line in lines if line]

    values = {
        "title": _extract_field("title", nonempty_lines) or _first_title_like_line(nonempty_lines),
        "department": _extract_field("department", nonempty_lines),
        "pay_rate": _extract_field("pay_rate", nonempty_lines),
        "hours": _extract_field("hours", nonempty_lines),
        "location": _extract_field("location", nonempty_lines),
    }
    sections = _extract_sections(lines)

    keywords = extract_keywords(text)
    software_tools = extract_software_tools(text)
    required_skills = _unique_preserve_order(
        sections.get("required_skills", []) + keywords
    )
    essential_duties = sections.get("essential_duties", []) or _fallback_duty_sentences(text)

    return ParsedJob(
        title=values["title"],
        department=values["department"],
        pay_rate=values["pay_rate"],
        hours=values["hours"],
        location=values["location"],
        minimum_qualifications=sections.get("minimum_qualifications", []),
        preferred_qualifications=sections.get("preferred_qualifications", []),
        essential_duties=essential_duties,
        required_skills=required_skills,
        software_tools=software_tools,
        keywords=keywords,
    )


def _extract_field(field_name: str, lines: list[str]) -> str | None:
    for line in lines:
        for pattern in FIELD_PATTERNS[field_name]:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                value = clean_list_item(match.group("value"))
                if value:
                    return value
    return None


def _first_title_like_line(lines: list[str]) -> str | None:
    for line in lines[:8]:
        cleaned = clean_list_item(line)
        if not cleaned:
            continue
        if len(cleaned) > 100:
            continue
        if _line_is_section_heading(cleaned):
            continue
        if re.search(r"^(job description|description|summary|apply now)$", cleaned, flags=re.IGNORECASE):
            continue
        return cleaned
    return None


def _extract_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "minimum_qualifications": [],
        "preferred_qualifications": [],
        "essential_duties": [],
        "required_skills": [],
    }
    current_section: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        heading = _section_for_line(line)
        if heading:
            current_section = heading
            inline = _inline_heading_content(line)
            if inline:
                sections[current_section].append(clean_list_item(inline))
            continue

        if _line_is_section_heading(line):
            current_section = None
            continue

        if current_section:
            item = clean_list_item(line)
            if item:
                sections[current_section].append(item)

    return {key: _unique_preserve_order(value) for key, value in sections.items()}


def _section_for_line(line: str) -> str | None:
    normalized = line.lower().strip(" :")
    for section, patterns in SECTION_PATTERNS.items():
        for pattern in patterns:
            if re.fullmatch(pattern, normalized, flags=re.IGNORECASE):
                return section
            if re.match(pattern + r"\s*[:\-]", normalized, flags=re.IGNORECASE):
                return section
    return None


def _inline_heading_content(line: str) -> str | None:
    parts = re.split(r"[:\-]", line, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return None


def _line_is_section_heading(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) > 80:
        return False
    if any(_section_for_line(stripped) == section for section in SECTION_PATTERNS):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z\s/&]+:?", stripped)) and len(stripped.split()) <= 5


def _fallback_duty_sentences(text: str) -> list[str]:
    duty_terms = [
        "responsible",
        "assist",
        "support",
        "coordinate",
        "develop",
        "manage",
        "maintain",
        "create",
        "document",
    ]
    sentences = []
    for sentence in sentence_split(text):
        lowered = sentence.lower()
        if any(term in lowered for term in duty_terms):
            sentences.append(sentence)
    return sentences[:8]


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_list_item(value)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result

