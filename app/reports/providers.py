"""OpenAI-compatible HTTP adapter and environment configuration for AI reports.

The adapter turns a base URL + API key + model into a plain ``invoke``
callable, so :class:`~app.reports.generators.AIReportGenerator` stays agnostic
of the transport. Environment configuration returns ``None`` when any of the
three required variables is missing, which the service treats as "AI not
configured" and falls back to the offline template.
"""

from __future__ import annotations

import json
import os
from typing import Callable

import httpx

from app.reports.generators import AIReportGenerator

_SYSTEM_PROMPT = (
    "你是一名教学反馈周报撰写助手。请根据用户提供的结构化数据生成一份严格的 "
    "JSON 对象，仅包含以下键：summary（字符串）、strengths（字符串数组）、"
    "concerns（字符串数组）、suggestions（字符串数组）。不要输出 JSON 以外的任何内容。"
)


def build_openai_invoke(
    base_url: str,
    api_key: str,
    model: str,
    transport: httpx.BaseTransport | None = None,
) -> Callable[[dict], dict]:
    """Return a callable that POSTs a prompt to an OpenAI Chat Completions API."""
    url = f"{base_url.rstrip('/')}/chat/completions"

    def invoke(payload: dict) -> dict:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)

    return invoke


def build_ai_generator_from_env() -> AIReportGenerator | None:
    """Build an AI generator from environment variables, or ``None`` if unset."""
    base_url = os.environ.get("REPORT_AI_BASE_URL")
    api_key = os.environ.get("REPORT_AI_API_KEY")
    model = os.environ.get("REPORT_AI_MODEL")

    if not base_url or not api_key or not model:
        return None

    return AIReportGenerator(build_openai_invoke(base_url, api_key, model))
