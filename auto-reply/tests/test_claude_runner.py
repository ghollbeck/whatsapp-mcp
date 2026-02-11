# ABOUTME: Tests for the Claude Code CLI runner.
# ABOUTME: Covers session mapping, command construction, and error handling.

import json
import pytest
from pathlib import Path

from claude_runner import ClaudeRunner, DEFAULT_ALLOWED_TOOLS, DEFAULT_DISALLOWED_TOOLS


@pytest.fixture
def runner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text("Test assistant persona.")
    return ClaudeRunner(
        workspace_dir=str(workspace),
        model="test-model",
        max_turns=3,
        timeout=30,
    )


class TestSessionMapping:
    def test_new_sender_has_no_session(self, runner):
        assert runner.get_session_id("unknown@s.whatsapp.net") is None

    def test_session_map_persists_to_disk(self, runner):
        runner._session_map["user@s.whatsapp.net"] = "session-abc-123"
        runner._save_session_map()

        # Create new runner pointing at same workspace
        runner2 = ClaudeRunner(
            workspace_dir=runner.workspace_dir,
            model="test-model",
        )
        assert runner2.get_session_id("user@s.whatsapp.net") == "session-abc-123"

    def test_clear_session_removes_mapping(self, runner):
        runner._session_map["user@s.whatsapp.net"] = "session-abc-123"
        runner._save_session_map()

        runner.clear_session("user@s.whatsapp.net")
        assert runner.get_session_id("user@s.whatsapp.net") is None

    def test_clear_nonexistent_session_is_noop(self, runner):
        runner.clear_session("nobody@s.whatsapp.net")

    def test_multiple_senders_have_separate_sessions(self, runner):
        runner._session_map["a@s.whatsapp.net"] = "session-a"
        runner._session_map["b@s.whatsapp.net"] = "session-b"
        runner._save_session_map()

        assert runner.get_session_id("a@s.whatsapp.net") == "session-a"
        assert runner.get_session_id("b@s.whatsapp.net") == "session-b"


class TestDefaultTools:
    def test_allowed_tools_include_read_only_operations(self):
        assert "Read" in DEFAULT_ALLOWED_TOOLS
        assert "Grep" in DEFAULT_ALLOWED_TOOLS
        assert "WebSearch" in DEFAULT_ALLOWED_TOOLS

    def test_disallowed_tools_block_write_operations(self):
        assert "Bash" in DEFAULT_DISALLOWED_TOOLS
        assert "Edit" in DEFAULT_DISALLOWED_TOOLS
        assert "Write" in DEFAULT_DISALLOWED_TOOLS

    def test_disallowed_tools_block_slack(self):
        assert "mcp__slack__*" in DEFAULT_DISALLOWED_TOOLS

    def test_perplexity_tools_allowed(self):
        perplexity_tools = [t for t in DEFAULT_ALLOWED_TOOLS if "perplexity" in t]
        assert len(perplexity_tools) >= 3


class TestRunnerConfig:
    def test_workspace_dir_resolves_to_absolute(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner = ClaudeRunner(workspace_dir=str(workspace))
        assert Path(runner.workspace_dir).is_absolute()

    def test_custom_tools_override_defaults(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        runner = ClaudeRunner(
            workspace_dir=str(workspace),
            allowed_tools=["Read", "WebSearch"],
            disallowed_tools=["Bash"],
        )
        assert runner.allowed_tools == ["Read", "WebSearch"]
        assert runner.disallowed_tools == ["Bash"]


class TestGenerateReplyErrorCases:
    @pytest.mark.asyncio
    async def test_timeout_returns_friendly_message(self, runner):
        # Set impossibly short timeout
        runner.timeout = 0.001
        reply = await runner.generate_reply("user@s.whatsapp.net", "Hello")
        # Should return an error message, not raise
        assert isinstance(reply, str)
        assert len(reply) > 0
