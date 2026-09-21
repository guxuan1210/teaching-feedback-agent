from __future__ import annotations

import asyncio
import inspect
import logging
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Callable

from fastapi import APIRouter, HTTPException, Request, Response

from app.wecom_customer.config import WecomCustomerConfig

logger = logging.getLogger(__name__)
MAX_CALLBACK_BODY_BYTES = 1024 * 1024


def create_router(
    config: WecomCustomerConfig,
    crypto,
    token_handler: Callable[[str], object],
    *,
    max_body_bytes: int = MAX_CALLBACK_BODY_BYTES,
) -> APIRouter:
    """Build callback endpoints with a bounded, process-local replay cache.

    Multi-worker deployments need a shared idempotency store for cross-process
    replay suppression; the upstream sync cursor remains the durable recovery path.
    """
    if max_body_bytes < 1:
        raise ValueError("max_body_bytes must be positive")
    router = APIRouter()
    seen_tokens: OrderedDict[str, None] = OrderedDict()
    token_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

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
        if not signature or not timestamp or not nonce:
            raise HTTPException(status_code=400, detail="Invalid callback")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_body_bytes:
                    raise HTTPException(status_code=413, detail="Callback body too large")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid callback") from None
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > max_body_bytes:
                raise HTTPException(status_code=413, detail="Callback body too large")
            body.extend(chunk)
        if not body:
            raise HTTPException(status_code=400, detail="Invalid callback")
        try:
            decrypted = crypto.decrypt_message(bytes(body), signature, timestamp, nonce)
            root = ET.fromstring(decrypted)
            sync_token = (root.findtext("Token") or "").strip()
            if not sync_token:
                raise ValueError("missing sync token")
        except Exception as exc:
            logger.warning("WeCom customer callback decryption failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=403, detail="Invalid callback") from None

        if sync_token not in seen_tokens:
            token_lock = token_locks.get(sync_token)
            if token_lock is None:
                token_lock = asyncio.Lock()
                token_locks[sync_token] = token_lock
            token_locks.move_to_end(sync_token)
            async with token_lock:
                if sync_token not in seen_tokens:
                    try:
                        if inspect.iscoroutinefunction(token_handler):
                            await token_handler(sync_token)
                        else:
                            result = await asyncio.to_thread(token_handler, sync_token)
                            if inspect.isawaitable(result):
                                await result
                    except Exception as exc:
                        logger.warning("WeCom customer callback handler failed (%s)", type(exc).__name__)
                        raise HTTPException(status_code=500, detail="Callback processing failed") from None
                    seen_tokens[sync_token] = None
                    seen_tokens.move_to_end(sync_token)
                    token_locks.pop(sync_token, None)
                    while len(seen_tokens) > 5000:
                        oldest, _ = seen_tokens.popitem(last=False)
                        token_locks.pop(oldest, None)
        return Response(content="success", media_type="text/plain")

    return router
