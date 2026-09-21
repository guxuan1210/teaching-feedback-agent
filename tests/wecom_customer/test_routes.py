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


def test_invalid_callback_signature_is_rejected():
    class BadCrypto(FakeCrypto):
        def verify_url(self, *args):
            raise ValueError("bad signature")

    app = FastAPI()
    app.include_router(create_router(CONFIG, BadCrypto(), lambda token: None))
    response = TestClient(app).get("/wecom/customer/callback?msg_signature=x&timestamp=1&nonce=n&echostr=x")
    assert response.status_code == 403
