from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
import httpx

from app.wecom_customer.config import WecomCustomerConfig

logger = logging.getLogger(__name__)
_API_ROOT = "https://qyapi.weixin.qq.com/cgi-bin"


class WecomApiError(RuntimeError):
    """An upstream API failure with no credential or message content attached."""


@dataclass(frozen=True)
class SyncResult:
    next_cursor: str | None
    messages: list[dict[str, Any]]
    has_more: bool = False


class WecomCustomerClient:
    def __init__(
        self,
        config: WecomCustomerConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
        timeout: float = 15.0,
    ) -> None:
        if not config.enabled or not config.corp_id or not config.corp_secret or not config.open_kfid:
            raise ValueError("微信客服客户端需要完整且已启用的配置")
        self.config = config
        self._client = httpx.Client(transport=transport, timeout=timeout)
        self._clock = clock
        self._access_token: str | None = None
        self._refresh_at = 0.0

    def close(self) -> None:
        self._client.close()

    def _token(self) -> str:
        if self._access_token is None or self._clock() >= self._refresh_at:
            try:
                response = self._client.get(
                    f"{_API_ROOT}/gettoken",
                    params={"corpid": self.config.corp_id, "corpsecret": self.config.corp_secret},
                )
                payload = self._json(response)
                token = payload.get("access_token")
                if not token:
                    raise WecomApiError("微信客服凭证获取失败")
                expires_in = int(payload.get("expires_in", 7200))
            except Exception as exc:
                if isinstance(exc, WecomApiError):
                    raise
                logger.warning("WeCom customer token request failed (%s)", type(exc).__name__)
                raise WecomApiError("微信客服凭证获取失败") from None
            self._access_token = token
            self._refresh_at = self._clock() + max(0, expires_in - 60)
        return self._access_token

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("errcode", 0) != 0:
                raise WecomApiError("微信客服 API 返回错误")
            return payload
        except WecomApiError:
            raise
        except Exception as exc:
            logger.warning("WeCom customer API request failed (%s)", type(exc).__name__)
            raise WecomApiError("微信客服 API 请求失败") from None

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(
                f"{_API_ROOT}/{endpoint}",
                params={"access_token": self._token()},
                json=payload,
            )
            return self._json(response)
        except WecomApiError:
            raise
        except Exception as exc:
            logger.warning("WeCom customer API request failed (%s)", type(exc).__name__)
            raise WecomApiError("微信客服 API 请求失败") from None

    def sync_messages(self, cursor: str | None, token: str) -> SyncResult:
        result = self._post(
            "kf/sync_msg",
            {"cursor": cursor or "", "token": token, "limit": 100},
        )
        return SyncResult(
            next_cursor=result.get("next_cursor"),
            messages=result.get("msg_list", []),
            has_more=bool(result.get("has_more", 0)),
        )

    def send_text(self, external_user_id: str, content: str) -> str:
        result = self._post(
            "kf/send_msg",
            {"touser": external_user_id, "open_kfid": self.config.open_kfid,
             "msgtype": "text", "text": {"content": content}},
        )
        return str(result.get("msgid", ""))

    def upload_image(self, content: bytes, filename: str) -> str:
        token = self._token()
        try:
            response = self._client.post(
                f"{_API_ROOT}/media/upload",
                params={"access_token": token, "type": "image"},
                files={"media": (filename, content, "application/octet-stream")},
            )
            result = self._json(response)
        except WecomApiError:
            raise
        except Exception as exc:
            logger.warning("WeCom customer image upload failed (%s)", type(exc).__name__)
            raise WecomApiError("微信客服图片上传失败") from None
        media_id = result.get("media_id")
        if not media_id:
            raise WecomApiError("微信客服图片上传失败")
        return str(media_id)

    def send_image(self, external_user_id: str, media_id: str) -> str:
        result = self._post(
            "kf/send_msg",
            {"touser": external_user_id, "open_kfid": self.config.open_kfid,
             "msgtype": "image", "image": {"media_id": media_id}},
        )
        return str(result.get("msgid", ""))
