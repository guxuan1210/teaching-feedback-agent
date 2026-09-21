from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class WecomCustomerConfig:
    enabled: bool
    corp_id: str | None
    corp_secret: str | None
    open_kfid: str | None
    callback_token: str | None
    callback_aes_key: str | None


_ENV_FIELDS = (
    ("WECOM_CUSTOMER_CORP_ID", "corp_id"),
    ("WECOM_CUSTOMER_SECRET", "corp_secret"),
    ("WECOM_CUSTOMER_OPEN_KFID", "open_kfid"),
    ("WECOM_CUSTOMER_CALLBACK_TOKEN", "callback_token"),
    ("WECOM_CUSTOMER_CALLBACK_AES_KEY", "callback_aes_key"),
)


def _enabled(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized == "":
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("WECOM_CUSTOMER_ENABLED 必须是 true/false、1/0、yes/no 或 on/off")


def load_customer_config(source: Mapping[str, str | None]) -> WecomCustomerConfig:
    enabled = _enabled(source.get("WECOM_CUSTOMER_ENABLED"))
    values = {name: (source.get(env_name) or "").strip() or None for env_name, name in _ENV_FIELDS}
    if enabled:
        for env_name, name in _ENV_FIELDS:
            if values[name] is None:
                raise ValueError(f"启用微信客服时必须配置 {env_name}")
        if len(values["callback_aes_key"] or "") != 43:
            raise ValueError("WECOM_CUSTOMER_CALLBACK_AES_KEY 必须是 43 位 EncodingAESKey")
    return WecomCustomerConfig(enabled=enabled, **values)
