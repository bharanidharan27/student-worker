"""Capture a manually authenticated ASU Workday browser session."""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_WORKDAY_URL = "https://www.myworkday.com/asu/d/task/1422$3898.htmld"
DEFAULT_AUTH_STATE_PATH = Path("playwright/.auth/asu_workday.json")


def ensure_auth_state_parent(auth_state_path: Path = DEFAULT_AUTH_STATE_PATH) -> Path:
    auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    return auth_state_path


def capture_login_state(
    workday_url: str = DEFAULT_WORKDAY_URL,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    browser_name: str = "chromium",
    slow_mo_ms: int = 0,
) -> Path:
    """Open Workday for manual SSO/MFA, then save Playwright storage state."""

    ensure_auth_state_parent(auth_state_path)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `playwright install` first."
        ) from exc

    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        browser = browser_type.launch(headless=False, slow_mo=slow_mo_ms)
        context = browser.new_context()
        page = context.new_page()
        page.goto(workday_url, wait_until="domcontentloaded", timeout=60_000)

        print("A browser window is open for ASU Workday.")
        print("Log in manually with ASU SSO and Duo/MFA.")
        print("Wait until the student jobs page is fully loaded.")
        input("Press Enter here after the jobs page loads...")

        context.storage_state(path=str(auth_state_path))
        browser.close()

    return auth_state_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manually log in to ASU Workday and save local browser state."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_WORKDAY_URL,
        help="ASU Workday student jobs URL.",
    )
    parser.add_argument(
        "--auth-state-path",
        type=Path,
        default=DEFAULT_AUTH_STATE_PATH,
        help="Where to save Playwright auth state.",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Playwright browser engine to launch.",
    )
    parser.add_argument(
        "--slow-mo-ms",
        type=int,
        default=0,
        help="Optional Playwright slow motion delay in milliseconds.",
    )
    args = parser.parse_args(argv)

    try:
        saved_path = capture_login_state(
            workday_url=args.url,
            auth_state_path=args.auth_state_path,
            browser_name=args.browser,
            slow_mo_ms=args.slow_mo_ms,
        )
    except RuntimeError as error:
        print(f"Error: {error}")
        return 1

    print(f"Saved auth state to {saved_path}")
    print("Keep this file local. Do not commit it to Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

