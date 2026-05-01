from pathlib import Path

from src.auth.login_capture import ensure_auth_state_parent
from src.auth.session_check import auth_state_exists, evaluate_session_page


def test_ensure_auth_state_parent_creates_directory(tmp_path: Path) -> None:
    auth_path = tmp_path / "playwright" / ".auth" / "asu_workday.json"

    result = ensure_auth_state_parent(auth_path)

    assert result == auth_path
    assert auth_path.parent.exists()


def test_auth_state_exists_requires_nonempty_file(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"

    assert auth_state_exists(auth_path) is False

    auth_path.write_text("", encoding="utf-8")
    assert auth_state_exists(auth_path) is False

    auth_path.write_text('{"cookies":[]}', encoding="utf-8")
    assert auth_state_exists(auth_path) is True


def test_evaluate_session_page_detects_login_page() -> None:
    assert evaluate_session_page(
        "https://weblogin.asu.edu/cas/login",
        "ASURITE User ID Password Sign In",
    ) is False


def test_evaluate_session_page_accepts_workday_jobs_page() -> None:
    assert evaluate_session_page(
        "https://www.myworkday.com/asu/d/task/1422$3898.htmld",
        "Workday Student Jobs Search Results",
    ) is True

