"""Keyword extraction for local rule-based parsing and scoring."""

from __future__ import annotations

import re


KEYWORD_PATTERNS: dict[str, list[str]] = {
    "Python": [r"\bpython\b"],
    "Java": [r"\bjava\b"],
    "React": [r"\breact\b"],
    "SQL": [r"\bsql\b", r"\bdatabase(s)?\b"],
    "LangChain": [r"\blangchain\b"],
    "Docker": [r"\bdocker\b"],
    "AWS": [r"\baws\b", r"\bamazon web services\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b"],
    "API Development": [r"\bapi(s)?\b", r"\brest\b"],
    "Backend Systems": [r"\bbackend\b", r"\bserver-side\b"],
    "Distributed Systems": [r"\bdistributed systems?\b"],
    "Machine Learning": [r"\bmachine learning\b", r"\bml\b"],
    "AI Research": [r"\bai research\b", r"\bartificial intelligence\b"],
    "Data Analysis": [r"\bdata analysis\b", r"\banalytics\b"],
    "Automation": [r"\bautomation\b", r"\bscript(ing)?\b"],
    "Product Requirements": [r"\bproduct requirements?\b", r"\bprd\b"],
    "User Stories": [r"\buser stories\b", r"\buser story\b"],
    "QA Testing": [r"\bqa\b", r"\bquality assurance\b", r"\btesting\b"],
    "Documentation": [r"\bdocumentation\b", r"\brecord keeping\b"],
    "Jira": [r"\bjira\b"],
    "Google Workspace": [r"\bgoogle workspace\b", r"\bgoogle docs\b", r"\bgoogle sheets\b"],
    "Microsoft Office": [r"\bmicrosoft office\b", r"\bexcel\b", r"\bword\b", r"\bpowerpoint\b"],
    "Customer Service": [r"\bcustomer service\b", r"\bcustomer support\b"],
    "Communication": [r"\bcommunication\b", r"\bphone\b", r"\bemail\b"],
    "Data Entry": [r"\bdata entry\b"],
    "Front Desk": [r"\bfront desk\b", r"\breception\b"],
    "Student Support": [r"\bstudent support\b", r"\bpeer support\b", r"\bambassador\b"],
    "Confidential Data": [r"\bconfidential\b", r"\bprivacy\b"],
    "Inventory": [r"\binventory\b"],
    "Billing": [r"\bbilling\b"],
}


SOFTWARE_KEYWORDS = {
    "Python",
    "Java",
    "React",
    "SQL",
    "LangChain",
    "Docker",
    "AWS",
    "Kubernetes",
    "Jira",
    "Google Workspace",
    "Microsoft Office",
}


def contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def extract_keywords(text: str, max_keywords: int = 30) -> list[str]:
    matches: list[str] = []
    for keyword, patterns in KEYWORD_PATTERNS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            matches.append(keyword)
    return matches[:max_keywords]


def extract_software_tools(text: str) -> list[str]:
    keywords = extract_keywords(text)
    return [keyword for keyword in keywords if keyword in SOFTWARE_KEYWORDS]


def count_term_hits(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term.lower() in lowered)

