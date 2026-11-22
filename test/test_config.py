import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DEFAULT_PATH, load_config


@pytest.fixture
def local_settings():
    data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
    data["mysql"]["password"] = "test-password"
    data["mcp"]["read_token"] = "x" * 32
    data["qwen"]["api_key"] = "test-key"
    return data


def test_load_config_from_json(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "mysql": {"host": "db", "port": 3306, "user": "demo", "password": "secret"},
        "mcp": {"url": "http://localhost:8765/mcp", "port": 8765, "read_token": "x" * 32},
        "qwen": {"base_url": "http://localhost:8000/v1", "api_key": "test"},
    }), encoding="utf-8")
    config = load_config(path)
    assert config.mysql.host == "db"
    assert config.mcp.read_token == "x" * 32
    assert config.qwen.base_url.endswith("/v1")


def test_invalid_config_does_not_expose_secret(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"mysql":{"password":"secret"}}', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_config(path)
    assert "secret" not in str(error.value)


def test_policy_settings_are_loaded_from_json(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    data = {
        "mysql": {"host": "db", "port": 3306, "user": "demo", "password": "secret"},
        "mcp": {"url": "http://localhost:8765/mcp", "port": 8765, "read_token": "x" * 32},
        "qwen": {"base_url": "http://localhost:8000/v1", "api_key": "test"},
        "policy": {"milvus_uri": "http://db:19530", "milvus_token": "private", "model_base": "http://model:8000"},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("POLICY_MILVUS_URI", "http://wrong:1")
    config = load_config(path)
    assert config.policy.milvus_uri == "http://db:19530"
    assert config.policy.milvus_token == "private"
    assert config.policy.model_base == "http://model:8000"


def test_policy_settings_must_be_complete_without_exposing_credentials(tmp_path, local_settings):
    local_settings["policy"] = {"milvus_uri": "http://db:19530", "milvus_token": "private", "model_base": ""}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(local_settings), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_config(path)
    assert "private" not in str(error.value)


def test_policy_backend_uses_selected_configuration(tmp_path, monkeypatch, local_settings):
    import mcp_service.policy.backend as backend
    from config import use_config
    local_settings["policy"] = {"milvus_uri": "http://configured:19530", "milvus_token": "private", "model_base": "http://configured:8000/"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(local_settings), encoding="utf-8")
    calls = []
    monkeypatch.setattr(backend, "MilvusClient", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setenv("POLICY_MILVUS_URI", "http://wrong:1")
    monkeypatch.setenv("POLICY_MODEL_BASE", "http://wrong:2")
    use_config(path)
    try:
        client = backend.PolicyBackend()
        assert calls[0]["uri"] == "http://configured:19530"
        assert calls[0]["token"] == "private"
        assert client.base == "http://configured:8000"
    finally:
        use_config(None)


def test_policy_backend_requires_policy_section(tmp_path, local_settings):
    from config import use_config
    from mcp_service.policy.backend import PolicyBackend
    local_settings.pop("policy", None)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(local_settings), encoding="utf-8")
    use_config(path)
    try:
        assert load_config().policy is None
        with pytest.raises(ValueError, match="缺少 policy"):
            PolicyBackend()
    finally:
        use_config(None)
