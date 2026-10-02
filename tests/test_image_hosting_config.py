"""配置唯一 owner、稳定身份、秘密拆分与明确清空契约。"""
from copy import deepcopy

import pytest

from erp_web.context import get_context
from erp_web.services.config_service import public_app_config
from erp_web.services.image_hosting_config import normalize_image_hosting, target_fingerprint
from erp_web.schemas.image_hosting import ImageHostingError
from tests.image_hosting_test_utils import profile


@pytest.mark.parametrize("change", [{"type": "unknown"}, {"endpoint_url": "http://s3.example.test"}, {"endpoint_url": "https://user:secret@s3.example.test"},
                                  {"public_base_url": "https://images.example.test?token=secret"}, {"key_prefix": "a/../b"}, {"key_prefix": "a/%2e%2e/b"},
                                  {"key_prefix": "a/%2e%2e%2fb"}, {"endpoint_url": "https://s3.example.test/%2e%2e%2fprivate"},
                                  {"endpoint_url": "https://[broken"}, {"endpoint_url": "https://s3.example.test:0"},
                                  {"endpoint_url": "https://127.0.0.1"}, {"public_base_url": "https://169.254.169.254"}, {"extra": "unknown"}])
def test_invalid_fields_are_rejected_without_echoing_secrets(change):
    value = profile(**change)
    with pytest.raises(ImageHostingError) as error:
        normalize_image_hosting({"default_profile_id": "images-main", "profiles": [value]})
    assert "test-secret-key" not in str(error.value)


def test_unknown_default_duplicate_ids_and_unknown_config_fields_rejected():
    for value in ({"default_profile_id": "missing", "profiles": [profile()]}, {"profiles": [profile(), profile()]}, {"unexpected": True}):
        with pytest.raises(ImageHostingError):
            normalize_image_hosting(value)


def test_secrets_only_in_database_and_stable_after_reorder():
    context = get_context(); store = context.config
    first = store.save_image_hosting_profile({key: value for key, value in profile().items() if key != "id"})
    second = store.save_image_hosting_profile({key: value for key, value in profile(name="第二配置", access_key_id="second-key", secret_access_key="second-secret").items() if key != "id"})
    config = store.load_app_config()
    text = context.paths.app_config_path.read_text()
    assert not any(secret in text for secret in ("test-access-key", "test-secret-key", "second-key", "second-secret"))
    public = store.public_image_hosting()
    assert public["profiles"][0]["access_key_id_configured"] is True
    assert "access_key_id" not in public["profiles"][0]
    assert "secret_access_key" not in public["profiles"][0]
    assert "test-access-key" not in str(public_app_config(context.paths.app_dir, config))
    config["image_hosting"]["profiles"].reverse()
    store.save_app_config(config)
    loaded = {item["id"]: item for item in store.load_app_config()["image_hosting"]["profiles"]}
    assert loaded[first]["secret_access_key"] == "test-secret-key"
    assert loaded[second]["secret_access_key"] == "second-secret"
    store.save_image_hosting_profile({"id": first, "name": "改名", "access_key_id": "****", "secret_access_key": "****"})
    assert next(item for item in store.load_app_config()["image_hosting"]["profiles"] if item["id"] == first)["secret_access_key"] == "test-secret-key"
    store.save_image_hosting_profile({"id": first, "clear_secrets": ["access_key_id", "secret_access_key"]})
    with pytest.raises(ImageHostingError):
        store.set_image_hosting_default(first)
    store.set_image_hosting_default(second)
    with pytest.raises(ImageHostingError, match="先解除"):
        store.delete_image_hosting_profile(second)
    store.delete_image_hosting_profile(first)
    stored_secrets = context.db.load_runtime_secrets("app_config")
    assert "test-secret-key" not in str(stored_secrets)
    assert "second-secret" in str(stored_secrets)


def test_incomplete_configuration_saves_without_network_and_empty_secret_requires_clear():
    store = get_context().config
    identity = store.save_image_hosting_profile({"name": "尚未配置"})
    with pytest.raises(ImageHostingError):
        store.set_image_hosting_default(identity)
    with pytest.raises(ImageHostingError, match="明确清空"):
        store.save_image_hosting_profile({"id": identity, "secret_access_key": ""})
    with pytest.raises(ImageHostingError, match="不存在"):
        store.save_image_hosting_profile({"id": "forged"})


def test_target_identity_ignores_name_and_credential_rotation():
    original = profile()
    assert target_fingerprint(original) == target_fingerprint(profile(name="改名", secret_access_key="rotated"))
    for field in ("bucket", "endpoint_url", "region", "key_prefix", "public_base_url", "addressing_style"):
        changed = deepcopy(original); changed[field] = "different"
        assert target_fingerprint(changed) != target_fingerprint(original)
