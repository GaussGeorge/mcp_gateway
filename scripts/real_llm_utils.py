#!/usr/bin/env python3
"""Utilities for real-LLM smoke checks and runners."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import os


DEFAULT_LLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-4-flash"


@dataclass
class LLMConfig:
    base: str
    key: str
    model: str
    env_path: Optional[Path] = None


class MissingAPIKeyError(RuntimeError):
    """Raised when no LLM API key is configured."""


def load_project_dotenv(root_dir: Optional[Path] = None) -> Optional[Path]:
    """Load project .env into os.environ without overriding existing vars."""
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent
    env_path = root_dir / ".env"
    if not env_path.exists():
        return None

    with env_path.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value and len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value
    return env_path


def resolve_llm_config() -> LLMConfig:
    return LLMConfig(
        base=os.getenv("AGENT_LLM_BASE", os.getenv("LLM_API_BASE", DEFAULT_LLM_BASE)),
        key=os.getenv("AGENT_LLM_KEY", os.getenv("LLM_API_KEY", "")),
        model=os.getenv("AGENT_LLM_MODEL", os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)),
    )


def mask_secret(secret: str) -> str:
    if not secret:
        return "(missing)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def format_exception_message(exc: Exception, limit: int = 300) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return text[:limit]


def classify_llm_error(exc: Exception) -> str:
    if isinstance(exc, MissingAPIKeyError):
        return "missing key"
    if isinstance(exc, ImportError):
        return "sdk error"

    text = format_exception_message(exc, limit=500).lower()
    if any(token in text for token in ("api_key", "api key", "authentication", "unauthorized", "401", "invalid key")):
        return "auth error"
    if any(token in text for token in ("timeout", "timed out", "readtimeout", "read timeout", "connecttimeout")):
        return "timeout"
    if any(token in text for token in ("connection", "connecterror", "network", "dns", "name or service", "temporarily unavailable", "ssl")):
        return "network"
    return "sdk error"


def run_llm_preflight(timeout_seconds: float = 30.0, max_tokens: int = 8, prompt: str = "Reply with exactly: ok") -> dict:
    """Send one minimal chat completion to confirm the LLM endpoint works."""
    load_project_dotenv()
    cfg = resolve_llm_config()
    if not cfg.key:
        raise MissingAPIKeyError("AGENT_LLM_KEY / LLM_API_KEY is not configured")

    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise ImportError("real-LLM extras are missing; install openai and httpx") from exc

    client = OpenAI(
        api_key=cfg.key,
        base_url=cfg.base,
        http_client=httpx.Client(timeout=httpx.Timeout(timeout_seconds), trust_env=False),
    )

    started = time.perf_counter()
    response = client.chat.completions.create(
        model=cfg.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0,
    )
    elapsed = time.perf_counter() - started

    content = ""
    if response.choices:
        message = response.choices[0].message
        content = message.content or ""

    usage = {
        "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
        "completion_tokens": getattr(response.usage, "completion_tokens", None),
        "total_tokens": getattr(response.usage, "total_tokens", None),
    }
    return {
        "base": cfg.base,
        "model": cfg.model,
        "key_masked": mask_secret(cfg.key),
        "elapsed_seconds": elapsed,
        "response_text": content.strip(),
        "usage": usage,
    }
