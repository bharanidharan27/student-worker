"""Small launcher hint for the React/FastAPI dashboard."""

from __future__ import annotations


def main() -> None:
    print("Dashboard API: .venv\\Scripts\\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000")
    print("Dashboard UI:  cd frontend && npm run dev")


if __name__ == "__main__":
    main()
