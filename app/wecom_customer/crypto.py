from __future__ import annotations

from collections.abc import Callable


class WecomCallbackCrypto:
    """Small adapter around wechatpy's Enterprise WeChat callback crypto."""

    def __init__(
        self,
        corp_id: str,
        callback_token: str,
        callback_aes_key: str,
        *,
        crypto_factory: Callable | None = None,
    ) -> None:
        if crypto_factory is None:
            from wechatpy.enterprise.crypto import WeChatCrypto

            crypto_factory = WeChatCrypto
        self._crypto = crypto_factory(callback_token, callback_aes_key, corp_id)

    def verify_url(self, signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        return self._crypto.check_signature(signature, timestamp, nonce, echostr)

    def decrypt_message(
        self, body: str | bytes, signature: str, timestamp: str, nonce: str
    ) -> str:
        decrypted = self._crypto.decrypt_message(body, signature, timestamp, nonce)
        return decrypted.decode("utf-8") if isinstance(decrypted, bytes) else decrypted
