from __future__ import annotations

import inspect
import logging
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Callable

from fastapi import APIRouter, HTTPException, Request, Response

from app.wecom_customer.config import WecomCustomerConfig

logger = logging.getLogger(__name__)


def create_router(config: WecomCustomerConfig, crypto, token_handler: Callable[[str], object]) -> APIRouter:
    router = APIRouter()
    seen_tokens: OrderedDict[str, None] = OrderedDict()
    seen_lock = threading.Lock()

    def enabled() -> None:
        if not config.enabled:
            raise HTTPException(status_code=404, detail="Not found")

    @router.get("/wecom/customer/callback")
    def verify_callback(request: Request) -> Response:
        enabled()
        args = request.query_params
        required = ("msg_signature", "timestamp", "nonce", "echostr")
        if any(not args.get(key) for key in required):
            raise HTTPException(status_code=400, detail="Invalid callback")
        try:
            echo = crypto.verify_url(*(args[key] for key in required))
        except Exception as exc:
            logger.warning("WeCom customer callback verification failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=403, detail="Invalid callback") from None
        return Response(content=echo, media_type="text/plain")

    @router.post("/wecom/customer/callback")
    async def receive_callback(request: Request) -> Response:
        enabled()
        args = request.query_params
        signature, timestamp, nonce = (
            args.get("msg_signature"), args.get("timestamp"), args.get("nonce")
        )
        body = await request.body()
        if not signature or not timestamp or not nonce or not body:
            raise HTTPException(status_code=400, detail="Invalid callback")
        try:
            decrypted = crypto.decrypt_message(body, signature, timestamp, nonce)
            root = ET.fromstring(decrypted)
            sync_token = (root.findtext("Token") or "").strip()
            if not sync_token:
                raise ValueError("missing sync token")
        except Exception as exc:
            logger.warning("WeCom customer callback decryption failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=403, detail="Invalid callback") from None

        with seen_lock:
            duplicate = sync_token in seen_tokens
            if not duplicate:
                seen_tokens[sync_token] = None
                if len(seen_tokens) > 5000:
                    seen_tokens.popitem(last=False)
        if not duplicate:
            try:
                result = token_handler(sync_token)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                with seen_lock:
                    seen_tokens.pop(sync_token, None)
                logger.warning("WeCom customer callback handler failed (%s)", type(exc).__name__)
                raise HTTPException(status_code=500, detail="Callback processing failed") from None
        return Response(content="success", media_type="text/plain")

    return router
