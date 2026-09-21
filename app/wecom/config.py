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
    media_root: Path = Path("data/student_media")
    media_max_bytes: int = 10 * 1024 * 1024
    pending_media_minutes: int = 15


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_wecom_config(source: Mapping[str, str | None]) -> WecomConfig:
    enabled = _enabled(source.get("WECOM_BOT_ENABLED"))
    bot_id = (source.get("WECOM_BOT_ID") or "").strip() or None
    bot_secret = (source.get("WECOM_BOT_SECRET") or "").strip() or None
    public_base_url = (source.get("TEACHING_APP_BASE_URL") or "").strip() or None
    media_root = Path((source.get("STUDENT_MEDIA_ROOT") or "data/student_media").strip())
    try:
        media_max_bytes = int(source.get("STUDENT_MEDIA_MAX_BYTES") or 10 * 1024 * 1024)
        pending_media_minutes = int(source.get("PENDING_MEDIA_MINUTES") or 15)
    except (TypeError, ValueError) as exc:
        raise ValueError("图片存储大小和待确认时长必须是正整数") from exc
    if media_max_bytes < 1 or pending_media_minutes < 1:
        raise ValueError("图片存储大小和待确认时长必须是正整数")
    if enabled and (not bot_id or not bot_secret):
        raise ValueError(
            "启用企业微信机器人时必须同时配置 WECOM_BOT_ID 和 WECOM_BOT_SECRET"
        )
    return WecomConfig(
        enabled, bot_id, bot_secret, public_base_url,
        media_root, media_max_bytes, pending_media_minutes,
    )


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
