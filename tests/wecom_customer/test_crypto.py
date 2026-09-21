from app.wecom_customer.crypto import WecomCallbackCrypto


class FakeWeChatCrypto:
    def __init__(self, token, aes_key, corp_id):
        self.args = token, aes_key, corp_id

    def check_signature(self, signature, timestamp, nonce, echo):
        assert (signature, timestamp, nonce, echo) == ("sig", "1", "n", "cipher")
        return "verified-echo"

    def decrypt_message(self, body, signature, timestamp, nonce):
        assert (body, signature, timestamp, nonce) == ("encrypted", "sig", "1", "n")
        return "<xml><Token>sync-secret</Token></xml>"


def test_crypto_decrypts_echo_and_message():
    crypto = WecomCallbackCrypto("corp", "callback", "a" * 43, crypto_factory=FakeWeChatCrypto)
    assert crypto.verify_url("sig", "1", "n", "cipher") == "verified-echo"
    assert crypto.decrypt_message("encrypted", "sig", "1", "n").endswith("</xml>")
