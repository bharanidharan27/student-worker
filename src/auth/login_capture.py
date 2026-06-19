"""Capture a manually authenticated ASU Workday browser session."""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.auth.auth_meta import AuthMeta, write_auth_meta


DEFAULT_WORKDAY_URL = "https://www.myworkday.com/asu/d/task/1422$3898.htmld"
DEFAULT_AUTH_STATE_PATH = Path("playwright/.auth/asu_workday.json")

PROFILE_NAME_SELECTORS = [
    "[data-automation-id='userName']",
    "[data-automation-id='topBarUserName']",
    "[data-automation-id='userMenu']",
    "[data-automation-id='headerUserName']",
    "header [data-automation-id*='user' i]",
    "header [aria-label*='user' i]",
    "[data-testid='userName']",
    "[data-testid='user-name']",
    ".WDNF[data-automation-id='userName']",
    "[data-automation-id='meMenu']",
    "[data-automation-id='meMenuTrigger']",
]

PROFILE_EMAIL_SELECTORS = [
    "[data-automation-id='userEmail']",
    "[data-automation-id='meMenuEmail']",
    "[data-automation-id='userMenuEmail']",
]

PROFILE_MENU_TRIGGER_SELECTORS = [
    "[data-automation-id='meMenu']",
    "[data-automation-id='meMenuTrigger']",
    "[data-automation-id='userMenu']",
    "[data-automation-id='profileMenu']",
    "[data-automation-id*='profile' i]",
    "[data-automation-id*='user' i]",
    "button[aria-label*='profile' i]",
    "button[aria-label*='account' i]",
    "button[aria-label*='user' i]",
    "[role='button'][aria-label*='profile' i]",
    "[role='button'][aria-label*='account' i]",
    "[role='button'][aria-label*='user' i]",
]

PROFILE_PANEL_MARKERS = [
    "My Account",
    "Sitemap",
    "Favorites",
    "My Reports",
    "Documentation",
    "Sign Out",
]

PROFILE_NAME_EXCLUSIONS = {
    "asu",
    "documentation",
    "favorites",
    "find student jobs",
    "home",
    "my account",
    "my reports",
    "personal resources",
    "saved",
    "search",
    "sign out",
    "sitemap",
    "workday",
}


def ensure_auth_state_parent(auth_state_path: Path = DEFAULT_AUTH_STATE_PATH) -> Path:
    auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    return auth_state_path


def _clean_profile_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _profile_email_from_text(text: str | None) -> str | None:
    cleaned = _clean_profile_text(text)
    if not cleaned:
        return None
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", cleaned)
    return match.group(0) if match else None


def _looks_like_profile_name(value: str | None) -> bool:
    cleaned = _clean_profile_text(value)
    if not cleaned or len(cleaned) > 80:
        return False
    lowered = cleaned.lower()
    if lowered in PROFILE_NAME_EXCLUSIONS:
        return False
    if any(excluded in lowered for excluded in PROFILE_NAME_EXCLUSIONS):
        return False
    if _profile_email_from_text(cleaned) is not None:
        return False
    if re.search(r"[\d@:/\\]", cleaned):
        return False
    words = cleaned.split()
    return 2 <= len(words) <= 5 and all(any(char.isalpha() for char in word) for word in words)


def _profile_name_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lines = [_clean_profile_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    marker_indices = [
        index
        for index, line in enumerate(lines)
        if any(line.lower() == marker.lower() for marker in PROFILE_PANEL_MARKERS)
    ]
    candidate_lines = lines[: marker_indices[0]] if marker_indices else lines
    for line in reversed(candidate_lines[-12:]):
        if _looks_like_profile_name(line):
            return line
    return None


def _read_text(page, selectors: list[str], parser: Callable[[str | None], str | None]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            text = locator.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        parsed = parser(text)
        if parsed:
            return parsed
    return None


def _read_profile_panel_text(page) -> str | None:
    try:
        texts = page.evaluate(
            """
            () => {
              const markers = ["My Account", "Sitemap", "Favorites", "My Reports", "Documentation", "Sign Out"];
              const isVisible = (element) => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
              };
              const candidates = Array.from(document.querySelectorAll("body *"))
                .filter(isVisible)
                .map((element) => element.innerText ? element.innerText.trim() : "")
                .filter((text) => text && markers.filter((marker) => text.includes(marker)).length >= 2)
                .sort((left, right) => left.length - right.length);
              return candidates.slice(0, 10);
            }
            """
        )
    except Exception:
        return None
    if not isinstance(texts, list):
        return None
    text_candidates = [text for text in texts if isinstance(text, str) and text.strip()]
    for text in text_candidates:
        if _profile_name_from_text(text) or _profile_email_from_text(text):
            return text
    return text_candidates[0] if text_candidates else None


def _click_profile_icon_by_position(page) -> str | None:
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        width = int(viewport.get("width") or 1280)
    except Exception:
        width = 1280

    for x, y in ((width - 50, 38), (width - 54, 40), (width - 48, 50)):
        try:
            page.mouse.click(x, y)
            page.wait_for_timeout(500)
        except Exception:
            continue
        panel_text = _read_profile_panel_text(page)
        if panel_text:
            return panel_text
    return None


def _open_profile_menu(page) -> str | None:
    panel_text = _read_profile_panel_text(page)
    if panel_text:
        return panel_text

    for selector in PROFILE_MENU_TRIGGER_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 8)
        except Exception:
            continue
        for index in range(count):
            try:
                candidate = locator.nth(index)
                if not candidate.is_visible(timeout=500):
                    continue
                candidate.click(timeout=1_000)
                page.wait_for_timeout(500)
            except Exception:
                continue
            panel_text = _read_profile_panel_text(page)
            if panel_text:
                return panel_text
    return _click_profile_icon_by_position(page)


def _scrape_profile(page) -> tuple[str | None, str | None]:
    display_name = _read_text(page, PROFILE_NAME_SELECTORS, _profile_name_from_text)
    email = _read_text(page, PROFILE_EMAIL_SELECTORS, _profile_email_from_text)
    if not display_name or not email:
        panel_text = _open_profile_menu(page)
        display_name = display_name or _profile_name_from_text(panel_text)
        email = email or _profile_email_from_text(panel_text)
    return display_name, email


def refresh_auth_meta_from_saved_session(
    workday_url: str = DEFAULT_WORKDAY_URL,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    browser_name: str = "chromium",
    headless: bool = True,
    timeout_ms: int = 60_000,
) -> AuthMeta:
    """Open a saved Workday session and refresh the local profile sidecar."""

    if not auth_state_path.exists():
        return AuthMeta()

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and `playwright install` first."
        ) from exc

    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        browser = browser_type.launch(headless=headless)
        context = browser.new_context(storage_state=str(auth_state_path))
        page = context.new_page()
        try:
            page.goto(workday_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            display_name, email = _scrape_profile(page)
        finally:
            browser.close()

    meta = AuthMeta(
        display_name=display_name,
        email=email,
        captured_at=datetime.now().isoformat(timespec="seconds"),
    )
    write_auth_meta(auth_state_path, meta)
    return meta


def capture_login_state(
    workday_url: str = DEFAULT_WORKDAY_URL,
    auth_state_path: Path = DEFAULT_AUTH_STATE_PATH,
    browser_name: str = "chromium",
    slow_mo_ms: int = 0,
    wait_for_user: Callable[[str], None] | None = None,
    display_name: str | None = None,
    email: str | None = None,
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
        prompt = "Press Enter here after the jobs page loads..."
        if wait_for_user is None:
            input(prompt)
        else:
            wait_for_user(prompt)

        context.storage_state(path=str(auth_state_path))
        scraped_name, scraped_email = _scrape_profile(page)
        browser.close()

    resolved_name = display_name or scraped_name
    resolved_email = email or scraped_email
    write_auth_meta(
        auth_state_path,
        AuthMeta(
            display_name=resolved_name,
            email=resolved_email,
            captured_at=datetime.now().isoformat(timespec="seconds"),
        ),
    )

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
