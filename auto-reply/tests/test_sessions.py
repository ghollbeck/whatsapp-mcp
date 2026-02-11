# ABOUTME: Tests for the per-sender session manager.
# ABOUTME: Covers session creation, message storage, idle reset, compaction, and API format.

import json
import pytest
from datetime import datetime, timedelta

from sessions import SessionManager, SessionMessage, SessionMetadata


@pytest.fixture
def manager(tmp_path):
    return SessionManager(
        storage_dir=str(tmp_path / "sessions"),
        idle_reset_minutes=30,
        max_history_tokens=1000,
        compaction_target_tokens=200
    )


class TestSessionCreation:
    def test_creates_new_session(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net", "Test User")
        assert key == "whatsapp:user@s.whatsapp.net"
        assert key in manager._metadata
        assert manager._metadata[key].sender_name == "Test User"

    def test_returns_existing_session(self, manager):
        key1 = manager.get_or_create_session("user@s.whatsapp.net", "User")
        key2 = manager.get_or_create_session("user@s.whatsapp.net", "User")
        assert key1 == key2

    def test_different_jids_get_different_sessions(self, manager):
        key1 = manager.get_or_create_session("a@s.whatsapp.net")
        key2 = manager.get_or_create_session("b@s.whatsapp.net")
        assert key1 != key2


class TestMessageStorage:
    def test_add_and_retrieve_message(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        msg = SessionMessage(
            role="user",
            content="Hello there",
            timestamp=datetime.now().isoformat(),
            sender_jid="user@s.whatsapp.net",
            sender_name="User"
        )
        manager.add_message(key, msg)
        history = manager.get_history(key)
        assert len(history) == 1
        assert history[0].content == "Hello there"
        assert history[0].role == "user"

    def test_multiple_messages_in_order(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        for i in range(5):
            manager.add_message(key, SessionMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                timestamp=datetime.now().isoformat()
            ))
        history = manager.get_history(key)
        assert len(history) == 5
        assert history[0].content == "Message 0"
        assert history[4].content == "Message 4"

    def test_metadata_updates_on_message(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        manager.add_message(key, SessionMessage(
            role="user", content="Test message", timestamp=datetime.now().isoformat()
        ))
        meta = manager._metadata[key]
        assert meta.message_count == 1
        assert meta.estimated_tokens > 0


class TestIdleReset:
    def test_idle_session_gets_reset(self, tmp_path):
        manager = SessionManager(
            storage_dir=str(tmp_path / "sessions"),
            idle_reset_minutes=0  # immediate idle
        )
        key = manager.get_or_create_session("user@s.whatsapp.net", "User")
        manager.add_message(key, SessionMessage(
            role="user", content="Old message",
            timestamp=(datetime.now() - timedelta(minutes=5)).isoformat()
        ))

        # Force the last_activity to be old
        manager._metadata[key].last_activity = (
            datetime.now() - timedelta(minutes=1)
        ).isoformat()
        manager._save_metadata()

        key2 = manager.get_or_create_session("user@s.whatsapp.net", "User")
        assert key2 == key  # same key, but session was reset
        history = manager.get_history(key2)
        # After reset, there should be a reset system message
        assert any(m.type == "reset" for m in history)


class TestCompaction:
    def test_needs_compaction_when_over_threshold(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        # Add a lot of content to exceed max_history_tokens (1000)
        for i in range(50):
            manager.add_message(key, SessionMessage(
                role="user", content="x" * 100,
                timestamp=datetime.now().isoformat()
            ))
        assert manager.needs_compaction(key) is True

    def test_no_compaction_needed_for_short_session(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        manager.add_message(key, SessionMessage(
            role="user", content="Short message",
            timestamp=datetime.now().isoformat()
        ))
        assert manager.needs_compaction(key) is False

    def test_compact_replaces_history_with_summary(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        for i in range(10):
            manager.add_message(key, SessionMessage(
                role="user", content=f"Message {i}",
                timestamp=datetime.now().isoformat()
            ))

        manager.compact_session(key, "Summary of previous conversation.")
        history = manager.get_history(key)
        assert len(history) == 1
        assert history[0].type == "compaction"
        assert "Summary" in history[0].content


class TestAPIMessageFormat:
    def test_user_and_assistant_messages(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        manager.add_message(key, SessionMessage(
            role="user", content="Hello", timestamp=datetime.now().isoformat()
        ))
        manager.add_message(key, SessionMessage(
            role="assistant", content="Hi there!", timestamp=datetime.now().isoformat()
        ))

        api_msgs = manager.get_history_as_api_messages(key)
        assert len(api_msgs) == 2
        assert api_msgs[0] == {"role": "user", "content": "Hello"}
        assert api_msgs[1] == {"role": "assistant", "content": "Hi there!"}

    def test_compaction_message_becomes_user_context(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        manager.compact_session(key, "Previous conversation about weather.")

        api_msgs = manager.get_history_as_api_messages(key)
        assert len(api_msgs) == 1
        assert api_msgs[0]["role"] == "user"
        assert "Previous conversation about weather." in api_msgs[0]["content"]

    def test_system_reset_messages_excluded(self, manager):
        key = manager.get_or_create_session("user@s.whatsapp.net")
        manager.reset_session(key, reason="test")
        # Re-create after reset
        key = manager.get_or_create_session("user@s.whatsapp.net")
        history = manager.get_history(key)
        api_msgs = manager.get_history_as_api_messages(key)
        # Reset messages have role="system" and type="reset", should be excluded
        assert all(m["role"] in ("user", "assistant") for m in api_msgs)


class TestSessionPersistence:
    def test_metadata_survives_reload(self, tmp_path):
        storage = str(tmp_path / "sessions")
        m1 = SessionManager(storage_dir=storage)
        key = m1.get_or_create_session("user@s.whatsapp.net", "Test")
        m1.add_message(key, SessionMessage(
            role="user", content="Persisted message",
            timestamp=datetime.now().isoformat()
        ))

        m2 = SessionManager(storage_dir=storage)
        assert key in m2._metadata
        assert m2._metadata[key].message_count == 1

    def test_messages_survive_reload(self, tmp_path):
        storage = str(tmp_path / "sessions")
        m1 = SessionManager(storage_dir=storage)
        key = m1.get_or_create_session("user@s.whatsapp.net")
        m1.add_message(key, SessionMessage(
            role="user", content="Persistent", timestamp=datetime.now().isoformat()
        ))

        m2 = SessionManager(storage_dir=storage)
        history = m2.get_history(key)
        assert len(history) == 1
        assert history[0].content == "Persistent"
