# ABOUTME: Tests for configuration loading and validation.
# ABOUTME: Covers YAML loading, env var overrides, and Pydantic defaults.

import os
import pytest
import yaml

from config import load_config, AutoReplyConfig


class TestConfigDefaults:
    def test_default_config_has_sane_values(self):
        config = AutoReplyConfig()
        assert config.bridge.url == "http://localhost:8082/api"
        assert config.daemon.port == 8084
        assert config.claude.model == "claude-sonnet-4-5-20250929"
        assert config.claude.max_turns == 5
        assert config.claude.timeout == 120
        assert config.session.idle_reset_minutes == 60
        assert config.pairing.enabled is True
        assert config.security.block_groups is True
        assert config.security.rate_limit_seconds == 5.0

    def test_default_security_has_empty_allowed_recipients(self):
        config = AutoReplyConfig()
        assert config.security.allowed_recipients == []


class TestConfigFromYAML:
    def test_load_from_yaml_file(self, tmp_path):
        config_data = {
            "bridge": {"url": "http://custom:9999/api", "send_timeout": 30},
            "daemon": {"port": 9090},
            "claude": {"model": "claude-haiku-4-5-20251001", "max_turns": 3},
        }
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(str(config_file))
        assert config.bridge.url == "http://custom:9999/api"
        assert config.bridge.send_timeout == 30
        assert config.daemon.port == 9090
        assert config.claude.model == "claude-haiku-4-5-20251001"
        assert config.claude.max_turns == 3

    def test_load_missing_yaml_uses_defaults(self, tmp_path):
        config = load_config(str(tmp_path / "nonexistent.yaml"))
        assert config.bridge.url == "http://localhost:8082/api"
        assert config.daemon.port == 8084

    def test_load_empty_yaml_uses_defaults(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        config = load_config(str(config_file))
        assert config.daemon.port == 8084


class TestConfigEnvOverrides:
    def test_allowed_recipients_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHATSAPP_MCP_ALLOWED_RECIPIENT",
                          "491732328586@s.whatsapp.net, 1732328586@s.whatsapp.net")
        config = load_config(str(tmp_path / "missing.yaml"))
        assert len(config.security.allowed_recipients) == 2
        assert "491732328586@s.whatsapp.net" in config.security.allowed_recipients
        assert "1732328586@s.whatsapp.net" in config.security.allowed_recipients

    def test_allowed_recipients_strips_whitespace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHATSAPP_MCP_ALLOWED_RECIPIENT", "  abc@s.whatsapp.net ,  def@lid  ")
        config = load_config(str(tmp_path / "missing.yaml"))
        assert config.security.allowed_recipients == ["abc@s.whatsapp.net", "def@lid"]
