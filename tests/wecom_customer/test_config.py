import pytest

from app.wecom_customer.config import WecomCustomerConfig, load_customer_config


def test_disabled_customer_config_needs_no_credentials():
    assert load_customer_config({"WECOM_CUSTOMER_ENABLED": "false"}) == WecomCustomerConfig(
        False, None, None, None, None, None
    )


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " Yes "])
def test_enabled_config_accepts_explicit_true_values(value):
    with pytest.raises(ValueError, match="WECOM_CUSTOMER_CORP_ID"):
        load_customer_config({"WECOM_CUSTOMER_ENABLED": value})


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " No ", "", None])
def test_disabled_config_accepts_explicit_false_values(value):
    assert load_customer_config({"WECOM_CUSTOMER_ENABLED": value}).enabled is False


def test_invalid_nonempty_enabled_value_is_rejected():
    with pytest.raises(ValueError, match="WECOM_CUSTOMER_ENABLED"):
        load_customer_config({"WECOM_CUSTOMER_ENABLED": "sometimes"})


def test_enabled_customer_channel_requires_all_credentials():
    with pytest.raises(ValueError, match="WECOM_CUSTOMER_CORP_ID"):
        load_customer_config({"WECOM_CUSTOMER_ENABLED": "true"})


def test_enabled_customer_config_loads_credentials_without_whitespace():
    config = load_customer_config({
        "WECOM_CUSTOMER_ENABLED": "1",
        "WECOM_CUSTOMER_CORP_ID": " corp ",
        "WECOM_CUSTOMER_SECRET": " secret ",
        "WECOM_CUSTOMER_OPEN_KFID": " kf ",
        "WECOM_CUSTOMER_CALLBACK_TOKEN": " callback ",
        "WECOM_CUSTOMER_CALLBACK_AES_KEY": f" {'a' * 43} ",
    })
    assert config.enabled is True
    assert config.corp_id == "corp"
    assert config.callback_aes_key == "a" * 43
