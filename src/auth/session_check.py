"""Check whether the saved ASU Workday Playwright session still works."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.auth.login_capture import DEFAULT_AUTH_STATE_PATH, DEFAULT_WORKDAY_URL


LOGIN_URL_MARKERS = [
    "login",
    "signin",
    "sso",
    "saml",
    "cas",
    "duo",
    "weblogin",
    "asurite",
]

LOGIN_TEXT_MARKERS = [
    "sign in",
    "log in",
    "login",
    "asurite",
    "password",
    "duo",
    "multi-factor",
    "multifactor",
]

JOBS_TEXT_MARKERS = [
    "student",
    "jobs",
    "job",
    "workday",
    "search",
]


def auth_state_exists(auth_state_path: Path = DEFAULT_AUTH_STATE_PATH) -> bool:
    return auth_state_path.exists() and auth_state_path.is_file() and auth_state_path.stat().st_size > 0


def looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in LOGIN_URL_MARKERS)


def looks_like_login_page_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in LOGIN_TEXT_MARKERS)


def looks_like_jobs_page_text(text: str) -> bool:
    lowered = text.lower()
    return sum(1 for marker in JOBS_TEXT_MARKERS if marker in lowered) >= 2


def evaluate_session_page(url: str, text: str) -> bool:
    """Return True when a loaded page appears to be an authenticated jobs page."""

    if looks_like_login_url(url):
        return False
    if looks_like_login_page_text(text) and not looks_like_jobs_page_text(text):
        return False
    return looks_like_jobs_page_text(text) or "myworkday.com/asu" in url.lower()


def check_session(
    workday_url: str = DEFAULT_WORKDAY_URL,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> bool:
    if not auth_state_exists(auth_state_path):
        return False

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `playwright install` first."
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(auth_state_path))
        page = context.new_page()
        try:
            page.goto(workday_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            page_text = page.locator("body").inner_text(timeout=10_000)
            is_valid = evaluate_session_page(page.url, page_text)
        finally:
            browser.close()

    return is_valid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether the saved ASU Workday browser session is still valid."
    )
    parser.add_argument("--url", default=DEFAULT_WORKDAY_URL, help="ASU Workday student jobs URL.")
    parser.add_argument(
        "--auth-state-path",
        type=Path,
        default=DEFAULT_AUTH_STATE_PATH,
        help="Saved Playwright auth state path.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser while checking the session.",
    )
    args = parser.parse_args(argv)

    try:
        valid = check_session(
            workday_url=args.url,
            auth_state_path=args.auth_state_path,
            headless=not args.headed,
        )
    except RuntimeError as error:
        print(f"Error: {error}")
        return 1

    if valid:
        print("Saved Workday session looks valid.")
        return 0

    print("Saved Workday session is missing or expired. Run `python -m src.auth.login_capture`.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

