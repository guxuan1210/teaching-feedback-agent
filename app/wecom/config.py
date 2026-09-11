from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class WecomConfig:
    enabled: bool
    bot_id: str | None
    bot_secret: str | None
    public_base_url: str | None


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_wecom_config(source: Mapping[str, str | None]) -> WecomConfig:
    enabled = _enabled(source.get("WECOM_BOT_ENABLED"))
    bot_id = (source.get("WECOM_BOT_ID") or "").strip() or None
    bot_secret = (source.get("WECOM_BOT_SECRET") or "").strip() or None
    public_base_url = (source.get("TEACHING_APP_BASE_URL") or "").strip() or None
    if enabled and (not bot_id or not bot_secret):
        raise ValueError(
            "启用企业微信机器人时必须同时配置 WECOM_BOT_ID 和 WECOM_BOT_SECRET"
        )
    return WecomConfig(enabled, bot_id, bot_secret, public_base_url)


def load_wecom_config_from_dotenv(
    env_file: str | Path, environ: Mapping[str, str | None] | None = None
) -> WecomConfig:
    import os

    process_source = os.environ if environ is None else environ
    process_config = load_wecom_config(process_source)
    if "WECOM_BOT_ENABLED" in process_source:
        return process_config
    file_values = dotenv_values(env_file)
    return load_wecom_config(file_values)
