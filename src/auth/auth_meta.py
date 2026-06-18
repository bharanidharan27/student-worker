"""Sidecar metadata that lives next to the Playwright auth state file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AuthMeta:
    display_name: str | None = None
    email: str | None = None
    captured_at: str | None = None


def meta_path_for(auth_state_path: Path) -> Path:
    return auth_state_path.with_name(f"{auth_state_path.name}.meta.json")


def read_auth_meta(auth_state_path: Path) -> AuthMeta:
    path = meta_path_for(auth_state_path)
    if not path.exists():
        return AuthMeta()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AuthMeta()
    return AuthMeta(
        display_name=payload.get("display_name"),
        email=payload.get("email"),
        captured_at=payload.get("captured_at"),
    )


def write_auth_meta(auth_state_path: Path, meta: AuthMeta) -> Path:
    path = meta_path_for(auth_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
    return path
