"""Provider-agnostic JSON chat client for optional LLM assessments."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError


T = TypeVar("T", bound=BaseModel)


DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
}

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "nvidia": "meta/llama-3.1-70b-instruct",
    "claude": "claude-sonnet-4-5",
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4.1-mini",
}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str | None
    base_url: str
    model: str

    @property
    def available(self) -> bool:
        return bool(self.api_key)


class LLMJsonClient:
    """Small OpenAI-compatible JSON chat client with schema validation."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        timeout_s: float | None = None,
        max_http_retries: int | None = None,
    ) -> None:
        self.config = config or load_llm_config()
        self.timeout_s = timeout_s if timeout_s is not None else get_llm_env_float("LLM_TIMEOUT_SECONDS", 60.0)
        self.max_http_retries = (
            max_http_retries if max_http_retries is not None else get_llm_env_int("LLM_HTTP_RETRIES", 2)
        )

    @property
    def available(self) -> bool:
        return self.config.available

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        if not self.config.api_key:
            raise RuntimeError("LLM API key is not configured.")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        last_error: str | None = None
        for attempt in range(2):
            retry_note = "" if attempt == 0 else f"\nPrevious JSON validation error: {last_error}"
            payload = self._payload(messages, response_model, retry_note)
            response = self._post_with_retries(payload)
            text = _extract_openai_content(response.json())
            try:
                return response_model.model_validate_json(_extract_json_object(text))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)

        raise RuntimeError(f"LLM returned invalid JSON: {last_error}")

    def _payload(self, messages: list[dict[str, str]], response_model: type[BaseModel], retry_note: str) -> dict:
        schema = response_model.model_json_schema()
        patched_messages = list(messages)
        if retry_note:
            patched_messages.append({"role": "user", "content": retry_note})
        return {
            "model": self.config.model,
            "messages": patched_messages,
            "temperature": 0.1,
            "max_tokens": 2_500,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        }

    def _post_with_retries(self, payload: dict) -> httpx.Response:
        attempts = max(1, self.max_http_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = httpx.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_s,
                )
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt >= attempts - 1 or not _is_retryable_error(exc):
                    raise
                time.sleep(_retry_delay_seconds(exc, attempt))
        raise RuntimeError("LLM request failed.") from last_error


def load_llm_config(env_path: Path = Path(".env")) -> LLMConfig:
    dotenv = _read_dotenv(env_path)
    provider = _env("LLM_PROVIDER", dotenv, "gemini").lower()
    api_key = _env("LLM_API_KEY", dotenv, None)
    if not api_key and provider == "gemini":
        api_key = _env("GEMINI_API_KEY", dotenv, None)
    if not api_key and provider == "groq":
        api_key = _env("GROQ_API_KEY", dotenv, None)
    if not api_key and provider == "nvidia":
        api_key = _env("NVIDIA_API_KEY", dotenv, None)
    if not api_key and provider in {"claude", "anthropic"}:
        api_key = _env("ANTHROPIC_API_KEY", dotenv, None)

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=_env("LLM_BASE_URL", dotenv, DEFAULT_BASE_URLS.get(provider, DEFAULT_BASE_URLS["gemini"])),
        model=_env("LLM_MODEL", dotenv, DEFAULT_MODELS.get(provider, DEFAULT_MODELS["gemini"])),
    )


def get_llm_env_float(name: str, default: float, env_path: Path = Path(".env")) -> float:
    raw_value = _env(name, _read_dotenv(env_path), None)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def get_llm_env_int(name: str, default: int, env_path: Path = Path(".env")) -> int:
    raw_value = _env(name, _read_dotenv(env_path), None)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _env(name: str, dotenv: dict[str, str], default: str | None) -> str | None:
    return os.environ.get(name) or dotenv.get(name) or default


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _extract_openai_content(payload: dict) -> str:
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM response did not include choices[0].message.content.") from exc


def _extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response.")
    return match.group(0)


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, httpx.TimeoutException):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code in {408, 409, 425, 429} or status_code >= 500
    return False


def _retry_delay_seconds(error: Exception, attempt: int) -> float:
    if isinstance(error, httpx.HTTPStatusError):
        retry_after = error.response.headers.get("retry-after")
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return min(30.0, 2.0**attempt)
