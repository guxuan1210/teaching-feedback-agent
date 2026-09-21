import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.wecom_customer.config import WecomCustomerConfig
from app.wecom_customer.routes import create_router


CONFIG = WecomCustomerConfig(True, "corp", "secret", "kf", "callback", "a" * 43)


class FakeCrypto:
    def verify_url(self, signature, timestamp, nonce, echo):
        return "verified-echo"

    def decrypt_message(self, body, signature, timestamp, nonce):
        return b"<xml><Token>sync-1</Token></xml>"


def test_callback_verification_uses_decrypted_echo():
    app = FastAPI()
    app.include_router(create_router(CONFIG, FakeCrypto(), lambda token: None))
    response = TestClient(app).get(
        "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n&echostr=cipher"
    )
    assert response.text == "verified-echo"


def test_callback_replays_a_sync_token_without_running_handler_twice():
    calls = []
    app = FastAPI()
    app.include_router(create_router(CONFIG, FakeCrypto(), calls.append))
    client = TestClient(app)
    response = client.post("/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n", content="encrypted")
    again = client.post("/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n", content="encrypted")
    assert (response.text, again.text, calls) == ("success", "success", ["sync-1"])


def test_concurrent_duplicate_waits_for_first_success():
    started = threading.Event()
    duplicate_decrypted = threading.Event()
    duplicate_done = threading.Event()
    release = threading.Event()
    calls = []

    class CountingCrypto(FakeCrypto):
        def __init__(self):
            self.decrypt_count = 0
            self.lock = threading.Lock()

        def decrypt_message(self, body, signature, timestamp, nonce):
            with self.lock:
                self.decrypt_count += 1
                if self.decrypt_count == 2:
                    duplicate_decrypted.set()
            return super().decrypt_message(body, signature, timestamp, nonce)

    async def handler(token):
        calls.append(token)
        started.set()
        await asyncio.to_thread(release.wait)

    app = FastAPI()
    app.include_router(create_router(CONFIG, CountingCrypto(), handler))

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        def post(is_duplicate=False):
            response = client.post(
                "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n",
                content="encrypted",
            )
            if is_duplicate:
                duplicate_done.set()
            return response

        first = pool.submit(post)
        assert started.wait(2)
        duplicate = pool.submit(post, True)
        assert duplicate_decrypted.wait(2)
        try:
            assert not duplicate_done.wait(0.1)
        finally:
            release.set()
        assert first.result(timeout=3).text == "success"
        assert duplicate.result(timeout=3).text == "success"
    assert calls == ["sync-1"]


def test_duplicate_after_failed_callback_retries_handler():
    calls = []

    def handler(token):
        calls.append(token)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")

    app = FastAPI()
    app.include_router(create_router(CONFIG, FakeCrypto(), handler))
    with TestClient(app) as client:
        path = "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n"
        failed = client.post(path, content="encrypted")
        retried = client.post(path, content="encrypted")
    assert (failed.status_code, retried.text, calls) == (500, "success", ["sync-1", "sync-1"])


def test_invalid_callback_signature_is_rejected():
    class BadCrypto(FakeCrypto):
        def verify_url(self, *args):
            raise ValueError("bad signature")

    app = FastAPI()
    app.include_router(create_router(CONFIG, BadCrypto(), lambda token: None))
    response = TestClient(app).get("/wecom/customer/callback?msg_signature=x&timestamp=1&nonce=n&echostr=x")
    assert response.status_code == 403


def test_callback_rejects_declared_oversized_body_before_decryption():
    class CountingCrypto(FakeCrypto):
        called = False

        def decrypt_message(self, *args):
            self.called = True
            return super().decrypt_message(*args)

    crypto = CountingCrypto()
    app = FastAPI()
    app.include_router(create_router(CONFIG, crypto, lambda token: None, max_body_bytes=8))
    response = TestClient(app).post(
        "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n",
        content=b"0123456789",
    )
    assert response.status_code == 413
    assert crypto.called is False


def test_callback_aborts_oversized_chunked_body():
    class CountingCrypto(FakeCrypto):
        called = False

        def decrypt_message(self, *args):
            self.called = True
            return super().decrypt_message(*args)

    crypto = CountingCrypto()
    app = FastAPI()
    app.include_router(create_router(CONFIG, crypto, lambda token: None, max_body_bytes=8))
    response = TestClient(app).post(
        "/wecom/customer/callback?msg_signature=sig&timestamp=1&nonce=n",
        content=iter([b"123456", b"7890"]),
    )
    assert response.status_code == 413
    assert crypto.called is False
