"""Background run orchestration for the dashboard API."""

from __future__ import annotations

import contextlib
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.storage.db import (
    DEFAULT_DB_PATH,
    append_automation_run_log,
    create_automation_run,
    get_automation_run,
    mark_stale_automation_runs_interrupted,
    update_automation_run,
)


RunAction = Callable[["RunContext"], dict[str, Any] | list[Any] | None]


class RunLogWriter:
    """Line-buffered writer that sends captured stdout/stderr to run logs."""

    def __init__(self, run_id: int, db_path: Path, level: str = "info") -> None:
        self.run_id = run_id
        self.db_path = db_path
        self.level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, value: str) -> int:
        if not value:
            return 0
        with self._lock:
            self._buffer += value
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._write_line(line)
        return len(value)

    def flush(self) -> None:
        with self._lock:
            if self._buffer.strip():
                self._write_line(self._buffer)
            self._buffer = ""

    def _write_line(self, line: str) -> None:
        message = line.strip()
        if message:
            append_automation_run_log(self.run_id, message, self.db_path, self.level)


@dataclass
class RunContext:
    run_id: int
    db_path: Path
    service: "AutomationService"

    def log(self, message: str, level: str = "info") -> None:
        append_automation_run_log(self.run_id, message, self.db_path, level)

    def set_step(self, step: str, status: str | None = None) -> None:
        update_automation_run(
            self.run_id,
            self.db_path,
            current_step=step,
            status=status,
        )

    def wait_for_continue(self, prompt: str, timeout_s: int = 30 * 60) -> None:
        self.service.wait_for_continue(self.run_id, prompt, timeout_s)

    def review_input(self, prompt: str) -> str:
        self.wait_for_continue(prompt)
        return ""


class AutomationService:
    """Single-worker queue for Workday browser automation."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workday-api")
        self._continue_events: dict[int, threading.Event] = {}
        self._futures: dict[int, Future] = {}
        self._lock = threading.Lock()

    def startup(self) -> None:
        interrupted = mark_stale_automation_runs_interrupted(self.db_path)
        if interrupted:
            # Startup logs are not tied to a new run, so the interrupted runs
            # carry their explanatory error field instead.
            pass

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    def submit(self, kind: str, params: dict[str, Any], action: RunAction) -> int:
        run_id = create_automation_run(kind, params=params, db_path=self.db_path)
        with self._lock:
            self._continue_events[run_id] = threading.Event()
        future = self.executor.submit(self._execute, run_id, action)
        with self._lock:
            self._futures[run_id] = future
        return run_id

    def continue_run(self, run_id: int) -> bool:
        row = get_automation_run(run_id, self.db_path)
        if row is None:
            return False

        with self._lock:
            event = self._continue_events.get(run_id)
        append_automation_run_log(run_id, "Continue signal received from UI.", self.db_path)
        if event is not None:
            event.set()
        return True

    def wait_for_continue(self, run_id: int, prompt: str, timeout_s: int) -> None:
        with self._lock:
            event = self._continue_events.setdefault(run_id, threading.Event())
            event.clear()

        update_automation_run(
            run_id,
            self.db_path,
            status="waiting_for_user",
            current_step=prompt,
        )
        append_automation_run_log(run_id, prompt, self.db_path)
        if not event.wait(timeout=timeout_s):
            raise TimeoutError("Timed out waiting for UI continue signal.")
        update_automation_run(
            run_id,
            self.db_path,
            status="running",
            current_step="Resuming after human confirmation.",
        )
        append_automation_run_log(run_id, "Resuming after UI confirmation.", self.db_path)

    def _execute(self, run_id: int, action: RunAction) -> None:
        context = RunContext(run_id=run_id, db_path=self.db_path, service=self)
        update_automation_run(
            run_id,
            self.db_path,
            status="running",
            current_step="Starting.",
            mark_started=True,
        )
        append_automation_run_log(run_id, "Run started.", self.db_path)
        stdout_writer = RunLogWriter(run_id, self.db_path, "info")
        stderr_writer = RunLogWriter(run_id, self.db_path, "error")
        try:
            with contextlib.redirect_stdout(stdout_writer), contextlib.redirect_stderr(stderr_writer):
                result = action(context)
            stdout_writer.flush()
            stderr_writer.flush()
            update_automation_run(
                run_id,
                self.db_path,
                status="completed",
                result=result if result is not None else {},
                current_step="Completed.",
                mark_finished=True,
            )
            append_automation_run_log(run_id, "Run completed.", self.db_path)
        except Exception as exc:  # pragma: no cover - exercised through API tests.
            stdout_writer.flush()
            stderr_writer.flush()
            error = str(exc) or exc.__class__.__name__
            append_automation_run_log(run_id, error, self.db_path, "error")
            append_automation_run_log(run_id, traceback.format_exc(), self.db_path, "error")
            update_automation_run(
                run_id,
                self.db_path,
                status="failed",
                error=error,
                current_step="Failed.",
                mark_finished=True,
            )
        finally:
            with self._lock:
                self._continue_events.pop(run_id, None)
                self._futures.pop(run_id, None)
