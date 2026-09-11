"""Unified LLM configuration and an OpenAI-compatible streaming client.

A single :func:`load_model_config` is the source of truth for model settings,
preferring the generic ``LLM_*`` variables and falling back to the legacy
``REPORT_AI_*`` variables. :class:`ChatProvider` turns that config into a
streaming ``chat/completions`` call, yielding text deltas.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
from dotenv import dotenv_values

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str
    model: str


def load_model_config(
    environ: Mapping[str, str | None] | None = None,
) -> ModelConfig | None:
    """Resolve model settings from ``LLM_*`` then legacy ``REPORT_AI_*``.

    Each group must be complete; partial groups are treated as unconfigured.
    """
    source = os.environ if environ is None else environ
    llm = (
        source.get("LLM_BASE_URL"),
        source.get("LLM_API_KEY"),
        source.get("LLM_MODEL"),
    )
    if all(llm):
        return ModelConfig(base_url=llm[0], api_key=llm[1], model=llm[2])

    report = (
        source.get("REPORT_AI_BASE_URL"),
        source.get("REPORT_AI_API_KEY"),
        source.get("REPORT_AI_MODEL"),
    )
    if all(report):
        logger.warning("REPORT_AI_* 已弃用，请改用 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
        return ModelConfig(base_url=report[0], api_key=report[1], model=report[2])

    return None


def load_model_config_from_dotenv(env_file: str | Path) -> ModelConfig | None:
    """Load model config without mutating process-wide environment variables.

    Complete process environment configuration takes precedence. Otherwise the
    specified dotenv file is evaluated as its own complete configuration group.
    """
    configured = load_model_config()
    if configured is not None:
        return configured
    return load_model_config(dotenv_values(env_file))


class ChatProvider:
    """Streams completions from an OpenAI-compatible Chat Completions endpoint."""

    def __init__(
        self,
        config: ModelConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    @property
    def model(self) -> str:
        return self._config.model

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield text deltas from a streamed ``chat/completions`` response."""
        url = f"{self._config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self._config.model,
            "messages": messages,
            "temperature": 0,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        timeout = httpx.Timeout(60.0, connect=10.0)
        with httpx.Client(timeout=timeout, transport=self._transport) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta


def build_chat_provider_from_env() -> ChatProvider | None:
    config = load_model_config()
    if config is None:
        return None
    return ChatProvider(config)
