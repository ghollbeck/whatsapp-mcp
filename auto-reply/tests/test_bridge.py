# ABOUTME: Tests for the Go bridge HTTP client.
# ABOUTME: Uses aiohttp test server to simulate the bridge REST API.

import asyncio
import json
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestServer

from bridge import BridgeClient


@pytest.fixture
def mock_bridge_app():
    """Create a mock Go bridge aiohttp app."""
    app = web.Application()
    app["sent_messages"] = []

    async def handle_send(request):
        payload = await request.json()
        app["sent_messages"].append(payload)

        recipient = payload.get("recipient", "")
        message = payload.get("message", "")

        if not recipient:
            return web.json_response(
                {"success": False, "message": "recipient required"}, status=400
            )

        return web.json_response({
            "success": True,
            "message": f"Sent to {recipient}"
        })

    async def handle_send_fail(request):
        return web.json_response(
            {"success": False, "message": "Bridge error"}, status=500
        )

    app.router.add_post("/api/send", handle_send)
    return app


@pytest_asyncio.fixture
async def mock_server(mock_bridge_app, aiohttp_server):
    server = await aiohttp_server(mock_bridge_app)
    return server


@pytest_asyncio.fixture
async def client(mock_server):
    base_url = f"http://localhost:{mock_server.port}/api"
    return BridgeClient(base_url=base_url, timeout=5)


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_success(self, client, mock_server):
        success, msg = await client.send_message("user@s.whatsapp.net", "Hello!")
        assert success is True
        assert "Sent" in msg

    @pytest.mark.asyncio
    async def test_send_message_stores_payload(self, client, mock_server):
        await client.send_message("user@s.whatsapp.net", "Test payload")
        sent = mock_server.app["sent_messages"]
        assert len(sent) == 1
        assert sent[0]["recipient"] == "user@s.whatsapp.net"
        assert sent[0]["message"] == "Test payload"

    @pytest.mark.asyncio
    async def test_send_to_missing_recipient_fails(self, client, mock_server):
        success, msg = await client.send_message("", "No recipient")
        assert success is False


class TestSendChunked:
    @pytest.mark.asyncio
    async def test_sends_all_chunks(self, client, mock_server):
        chunks = ["Part 1", "Part 2", "Part 3"]
        results = await client.send_chunked("user@s.whatsapp.net", chunks, delay=0.01)
        assert len(results) == 3
        assert all(success for success, _ in results)

    @pytest.mark.asyncio
    async def test_empty_chunks_returns_empty(self, client, mock_server):
        results = await client.send_chunked("user@s.whatsapp.net", [], delay=0)
        assert results == []


class TestConnectionFailure:
    @pytest.mark.asyncio
    async def test_connection_refused_returns_error(self):
        client = BridgeClient(base_url="http://localhost:1/api", timeout=1)
        success, msg = await client.send_message("user@s.whatsapp.net", "Hello")
        assert success is False
        assert "Cannot connect" in msg or "Unexpected error" in msg


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_with_running_server(self, client, mock_server):
        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_with_dead_server(self):
        client = BridgeClient(base_url="http://localhost:1/api", timeout=1)
        result = await client.health_check()
        assert result is False
