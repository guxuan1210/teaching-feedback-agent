import httpx

from app.wecom_customer.client import WecomCustomerClient
from app.wecom_customer.config import WecomCustomerConfig


def test_teacher_labeled_reply_uses_customer_text_delivery():
    payloads = []

    def transport(request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "token", "expires_in": 3600})
        if request.url.path.endswith("/kf/send_msg"):
            payloads.append(request.read())
            return httpx.Response(200, json={"errcode": 0, "msgid": "MSG-1"})
        return httpx.Response(404)

    config = WecomCustomerConfig(True, "corp", "secret", "kf", "token", "a" * 43)
    client = WecomCustomerClient(config, transport=httpx.MockTransport(transport), clock=lambda: 1000)

    result = client.send_text("EXT-PARENT", "【老师：王老师】已了解，谢谢反馈")

    assert result == "MSG-1"
    assert b"EXT-PARENT" in payloads[0]
    assert "【老师：王老师】".encode() in payloads[0]
    assert b"open_kfid" in payloads[0]
    client.close()
