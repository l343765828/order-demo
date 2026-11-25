import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import DEFAULT_PATH, load_config


@pytest.fixture
def settings():
    return {
        "mysql": {"host": "db", "port": 3306, "user": "demo", "password": "fixture-password"},
        "mcp": {"url": "http://localhost:8765/mcp", "port": 8765, "read_token": "x" * 32},
        "qwen": {"base_url": "http://qwen.test:8000/v1", "model": " qwen-test ",
                 "max_completion_tokens": 123, "timeout": 42},
        "policy": {"milvus_uri": "http://db:19530", "milvus_token": "fixture-token"},
        "embedding": {"url": "http://embed.test:9000/custom/encode", "model": " encode-test ", "timeout": 7},
        "reranker": {"url": "https://rank.test:8443/custom/rank", "model": " rank-test ", "timeout": 13},
    }


@pytest.fixture
def config_file(tmp_path):
    def write(data):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return write


def test_loads_complete_model_config(settings, config_file, monkeypatch):
    monkeypatch.setenv("POLICY_MILVUS_URI", "http://wrong:1")
    config = load_config(config_file(settings))
    assert config.policy.milvus_uri == "http://db:19530"
    assert (config.qwen.base_url, config.qwen.model, config.qwen.max_completion_tokens, config.qwen.timeout) == (
        "http://qwen.test:8000/v1", "qwen-test", 123, 42,
    )
    assert (config.embedding.url, config.embedding.model, config.embedding.timeout) == (
        "http://embed.test:9000/custom/encode", "encode-test", 7,
    )
    assert (config.reranker.url, config.reranker.model, config.reranker.timeout) == (
        "https://rank.test:8443/custom/rank", "rank-test", 13,
    )


def test_shipped_config_includes_all_three_models():
    config = load_config(DEFAULT_PATH)
    assert config.policy is not None
    assert config.embedding is not None
    assert config.reranker is not None


@pytest.mark.parametrize("legacy_fields", [{}, {"api_key": "legacy-test-value"}])
def test_loads_order_only_config_and_ignores_legacy_qwen_key(settings, config_file, legacy_fields):
    for section in ("policy", "embedding", "reranker"):
        settings.pop(section)
    settings["qwen"].update(legacy_fields)
    config = load_config(config_file(settings))
    assert config.mysql.host == "db"
    assert config.mcp.read_token == "x" * 32
    assert config.policy is config.embedding is config.reranker is None
    assert not hasattr(config.qwen, "api_key")


@pytest.mark.parametrize("section", ["embedding", "reranker"])
def test_policy_requires_both_model_sections(settings, config_file, section):
    del settings[section]
    with pytest.raises(ValueError, match="无法加载配置"):
        load_config(config_file(settings))


@pytest.mark.parametrize(("section", "field"), [
    ("qwen", "base_url"), ("qwen", "model"), ("qwen", "max_completion_tokens"), ("qwen", "timeout"),
    ("embedding", "url"), ("embedding", "model"), ("embedding", "timeout"),
    ("reranker", "url"), ("reranker", "model"), ("reranker", "timeout"),
])
def test_every_model_field_is_required(settings, config_file, section, field):
    del settings[section][field]
    with pytest.raises(ValueError, match="无法加载配置"):
        load_config(config_file(settings))


@pytest.mark.parametrize(("section", "field", "invalid"), [
    ("qwen", "base_url", "ftp://qwen.test/v1"),
    ("embedding", "url", "http://"),
    ("reranker", "url", "http:rank.test"),
    ("reranker", "url", "https://rank.test:65536/rank"),
    ("embedding", "url", r"http://embed.test\encode"),
    ("reranker", "url", "https://rank.test/custom/\x00rank"),
    ("qwen", "model", "  "),
    ("embedding", "model", 123),
    ("reranker", "model", ""),
    ("qwen", "max_completion_tokens", 0),
    ("qwen", "max_completion_tokens", "123"),
    ("qwen", "timeout", True),
    ("embedding", "timeout", 0),
    ("reranker", "timeout", 1.5),
])
def test_provided_models_are_validated_even_without_policy(settings, config_file, section, field, invalid):
    del settings["policy"]
    settings[section][field] = invalid
    with pytest.raises(ValueError, match="无法加载配置"):
        load_config(config_file(settings))


@pytest.mark.parametrize("section", ["embedding", "reranker"])
def test_optional_model_section_cannot_be_null(settings, config_file, section):
    del settings["policy"]
    settings[section] = None
    with pytest.raises(ValueError, match="无法加载配置"):
        load_config(config_file(settings))


@pytest.mark.parametrize(("section", "field"), [
    ("qwen", "extra"), ("embedding", "extra"), ("reranker", "extra"), ("policy", "model_base"),
])
def test_unknown_and_removed_fields_are_rejected_without_exposing_values(settings, config_file, section, field):
    settings[section][field] = "fixture-private-value"
    with pytest.raises(ValueError, match="无法加载配置") as error:
        load_config(config_file(settings))
    assert "fixture-private-value" not in str(error.value)
    assert "fixture-password" not in str(error.value)
    assert error.value.__suppress_context__


def test_invalid_policy_connection_is_rejected_without_exposing_credentials(settings, config_file):
    settings["policy"]["milvus_uri"] = " "
    with pytest.raises(ValueError, match="无法加载配置") as error:
        load_config(config_file(settings))
    assert "fixture-token" not in str(error.value)


def test_qwen_url_cannot_supply_implicit_http_credentials(settings, config_file):
    settings["qwen"]["base_url"] = "http://fixture:fixture-private-value@qwen.test/v1"
    with pytest.raises(ValueError, match="无法加载配置") as error:
        load_config(config_file(settings))
    assert "fixture-private-value" not in str(error.value)
