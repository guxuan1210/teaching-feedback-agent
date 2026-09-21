import json

import httpx

from app.wecom_customer.client import WecomCustomerClient
from app.wecom_customer.config import WecomCustomerConfig


CONFIG = WecomCustomerConfig(True, "corp", "secret", "kf", "callback", "a" * 43)


class MockWeCom:
    def __init__(self):
        self.token_requests = 0
        self.sent_messages = []

    def __call__(self, request):
        if request.url.path.endswith("/gettoken"):
            self.token_requests += 1
            return httpx.Response(200, json={"errcode": 0, "access_token": f"access-{self.token_requests}", "expires_in": 3600})
        if request.url.path.endswith("/kf/send_msg"):
            self.sent_messages.append(json.loads(request.content))
            return httpx.Response(200, json={"errcode": 0, "msgid": "sent-1"})
        if request.url.path.endswith("/kf/sync_msg"):
            return httpx.Response(200, json={"errcode": 0, "next_cursor": "next", "msg_list": [], "has_more": 0})
        if request.url.path.endswith("/media/upload"):
            return httpx.Response(200, json={"errcode": 0, "media_id": "media-1"})
        return httpx.Response(404)


def test_send_text_refreshes_token_once_on_expiry():
    mock = MockWeCom()
    now = [1000.0]
    api = WecomCustomerClient(CONFIG, transport=httpx.MockTransport(mock), clock=lambda: now[0])
    assert api.send_text("EXT1", "【机器人回复】你好") == "sent-1"
    now[0] = 4540.0
    assert api.send_text("EXT1", "再次回复") == "sent-1"
    assert mock.token_requests == 2
    assert len(mock.sent_messages) == 2


def test_client_sync_upload_and_send_image_use_expected_payloads():
    mock = MockWeCom()
    api = WecomCustomerClient(CONFIG, transport=httpx.MockTransport(mock), clock=lambda: 1000.0)
    result = api.sync_messages("cursor", "sync-token")
    assert (result.next_cursor, result.messages) == ("next", [])
    assert api.upload_image(b"image-bytes", "photo.png") == "media-1"
    assert api.send_image("EXT1", "media-1") == "sent-1"
    assert mock.sent_messages[-1]["msgtype"] == "image"
