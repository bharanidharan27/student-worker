"""Text cleanup helpers for pasted job descriptions."""

from __future__ import annotations

import re


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in normalize_whitespace(text).splitlines() if line.strip()]


def clean_list_item(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[\-*•\u2022\s]+", "", text)
    text = re.sub(r"^\d+[\.)]\s+", "", text)
    return text.strip(" ;")


def sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", normalize_whitespace(text))
    return [part.strip() for part in parts if part.strip()]

