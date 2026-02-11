# ABOUTME: Integration tests for the auto-reply daemon webhook handler.
# ABOUTME: Tests the full webhook pipeline with mocked Claude runner and bridge.

import json
import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web

from daemon import AutoReplyDaemon


@pytest.fixture
def config_dir(tmp_path):
    """Create a temp config directory with all required files."""
    import yaml

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text("Test assistant.")

    config_data = {
        "bridge": {"url": "http://localhost:19999/api", "send_timeout": 2},
        "daemon": {"port": 18084},
        "claude": {
            "model": "test-model",
            "max_turns": 3,
            "timeout": 10,
            "workspace_dir": str(workspace),
        },
        "session": {
            "storage_dir": str(tmp_path / "sessions"),
            "idle_reset_minutes": 60,
        },
        "pairing": {"enabled": False},
        "security": {"block_groups": True, "rate_limit_seconds": 0},
    }

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))

    return tmp_path, str(config_file)


@pytest.fixture
def daemon(config_dir, monkeypatch):
    tmp_path, config_path = config_dir
    monkeypatch.chdir(tmp_path)
    d = AutoReplyDaemon(config_path=config_path)
    return d


class TestWebhookEndpoint:
    @pytest.mark.asyncio
    async def test_webhook_accepts_valid_payload(self, daemon, aiohttp_client):
        client = await aiohttp_client(daemon.app)
        payload = {
            "message_id": "msg-001",
            "sender_jid": "user@s.whatsapp.net",
            "content": "Hello!",
            "is_from_me": False,
            "is_group": False,
            "timestamp": "2026-02-11T12:00:00",
        }
        resp = await client.post("/webhook/message", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_webhook_rejects_invalid_json(self, daemon, aiohttp_client):
        client = await aiohttp_client(daemon.app)
        resp = await client.post(
            "/webhook/message",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_webhook_secret_validation(self, daemon, aiohttp_client):
        daemon._webhook_secret = "test-secret-123"
        client = await aiohttp_client(daemon.app)

        # Without secret
        payload = {"sender_jid": "user@s.whatsapp.net", "content": "Hi"}
        resp = await client.post("/webhook/message", json=payload)
        assert resp.status == 401

        # With wrong secret
        resp = await client.post(
            "/webhook/message",
            json=payload,
            headers={"X-Webhook-Secret": "wrong"},
        )
        assert resp.status == 401

        # With correct secret
        resp = await client.post(
            "/webhook/message",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status == 200


class TestMessageProcessing:
    @pytest.mark.asyncio
    async def test_ignores_own_messages(self, daemon):
        payload = {
            "sender_jid": "me@s.whatsapp.net",
            "content": "My own message",
            "is_from_me": True,
            "is_group": False,
        }
        await daemon.process_message(payload)

    @pytest.mark.asyncio
    async def test_blocks_group_messages(self, daemon):
        payload = {
            "sender_jid": "group@g.us",
            "content": "Group message",
            "is_from_me": False,
            "is_group": True,
        }
        await daemon.process_message(payload)

    @pytest.mark.asyncio
    async def test_processes_valid_message(self, daemon):
        # Patch Claude runner and bridge
        daemon.claude.generate_reply = AsyncMock(return_value="Test reply from Claude Code")
        daemon.bridge.send_chunked = AsyncMock(return_value=[(True, "Sent")])

        payload = {
            "sender_jid": "user@s.whatsapp.net",
            "sender_name": "Test User",
            "content": "Hello, testing!",
            "is_from_me": False,
            "is_group": False,
        }
        await daemon.process_message(payload)

        # Claude runner should have been called with the message
        daemon.claude.generate_reply.assert_called_once_with(
            sender_jid="user@s.whatsapp.net",
            message="Hello, testing!",
            sender_name="Test User",
        )

        # Bridge should have sent the response
        daemon.bridge.send_chunked.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_media_message(self, daemon):
        daemon.claude.generate_reply = AsyncMock(return_value="I see you sent an image!")
        daemon.bridge.send_chunked = AsyncMock(return_value=[(True, "Sent")])

        payload = {
            "sender_jid": "user@s.whatsapp.net",
            "content": "",
            "media_type": "image",
            "is_from_me": False,
            "is_group": False,
        }
        await daemon.process_message(payload)

        # Claude runner should have received the media placeholder
        call_args = daemon.claude.generate_reply.call_args
        assert "[Sent a image message]" in call_args.kwargs.get("message", call_args[1].get("message", ""))


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_status(self, daemon, aiohttp_client):
        daemon.bridge.health_check = AsyncMock(return_value=True)
        client = await aiohttp_client(daemon.app)
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.2.0"
        assert "model" in data

    @pytest.mark.asyncio
    async def test_health_degraded_when_bridge_down(self, daemon, aiohttp_client):
        daemon.bridge.health_check = AsyncMock(return_value=False)
        client = await aiohttp_client(daemon.app)
        resp = await client.get("/health")
        data = await resp.json()
        assert data["status"] == "degraded"
        assert data["bridge_connected"] is False
