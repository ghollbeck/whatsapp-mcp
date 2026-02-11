# ABOUTME: Spawns Claude Code CLI sessions for generating WhatsApp replies.
# ABOUTME: Each sender gets a persistent session via --resume, with full MCP access.

import asyncio
import json
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger("claude-runner")

# Tools that are safe for a WhatsApp assistant (read-only operations)
DEFAULT_ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "mcp__perplexity__perplexity_ask",
    "mcp__perplexity__perplexity_search",
    "mcp__perplexity__perplexity_research",
    "mcp__perplexity__perplexity_reason",
    "mcp__mcp-deepwiki__deepwiki_fetch",
    "mcp__supabase__execute_sql",
    "mcp__supabase__search_docs",
    "mcp__supabase__list_tables",
    "mcp__supabase__list_projects",
    "mcp__github__get_file_contents",
    "mcp__github__get_issue",
    "mcp__github__get_pull_request",
    "mcp__github__search_code",
    "mcp__github__search_repositories",
    "mcp__github__search_issues",
    "mcp__github__list_issues",
    "mcp__github__list_commits",
    "mcp__github__list_pull_requests",
]

# Tools explicitly blocked — no write/execute operations from WhatsApp
DEFAULT_DISALLOWED_TOOLS = [
    "Bash",
    "Edit",
    "Write",
    "NotebookEdit",
    "Task",
    "mcp__slack__*",
    "mcp__puppeteer__*",
    "mcp__chrome-devtools__*",
    "mcp__github__create_*",
    "mcp__github__push_*",
    "mcp__github__merge_*",
    "mcp__github__fork_*",
    "mcp__github__update_*",
    "mcp__github__add_*",
    "mcp__supabase__apply_migration",
    "mcp__supabase__create_*",
    "mcp__supabase__delete_*",
    "mcp__supabase__pause_*",
    "mcp__supabase__restore_*",
    "mcp__supabase__deploy_*",
    "mcp__supabase__merge_*",
    "mcp__supabase__reset_*",
    "mcp__supabase__rebase_*",
]


class ClaudeRunner:
    def __init__(
        self,
        workspace_dir: str = "workspace",
        model: str = "claude-sonnet-4-5-20250929",
        max_turns: int = 5,
        timeout: int = 120,
        allowed_tools: Optional[list[str]] = None,
        disallowed_tools: Optional[list[str]] = None,
        mcp_config: Optional[str] = None,
    ):
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.allowed_tools = allowed_tools or DEFAULT_ALLOWED_TOOLS
        self.disallowed_tools = disallowed_tools or DEFAULT_DISALLOWED_TOOLS
        self.mcp_config = mcp_config

        # Persistent mapping: sender_jid -> claude session_id
        self._session_map: dict[str, str] = {}
        self._session_map_path = Path(workspace_dir) / ".session_map.json"
        self._load_session_map()

    def _load_session_map(self):
        if self._session_map_path.exists():
            with open(self._session_map_path) as f:
                self._session_map = json.load(f)
            logger.info("session_map_loaded", count=len(self._session_map))

    def _save_session_map(self):
        self._session_map_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._session_map_path, "w") as f:
            json.dump(self._session_map, f, indent=2)

    def get_session_id(self, sender_jid: str) -> Optional[str]:
        return self._session_map.get(sender_jid)

    def clear_session(self, sender_jid: str):
        if sender_jid in self._session_map:
            del self._session_map[sender_jid]
            self._save_session_map()
            logger.info("session_cleared", sender=sender_jid)

    async def generate_reply(self, sender_jid: str, message: str,
                              sender_name: str = "") -> str:
        session_id = self._session_map.get(sender_jid)

        cmd = [
            "claude",
            "-p",
            "--output-format", "json",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
        ]

        if session_id:
            cmd.extend(["--resume", session_id])

        if self.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.allowed_tools)])

        if self.disallowed_tools:
            cmd.extend(["--disallowedTools", ",".join(self.disallowed_tools)])

        if self.mcp_config:
            cmd.extend(["--mcp-config", self.mcp_config])

        # Add sender context as system prompt appendix
        system_append = (
            f"You are chatting with {sender_name or 'someone'} on WhatsApp. "
            "Keep your response concise and conversational. "
            "No markdown formatting."
        )
        cmd.extend(["--append-system-prompt", system_append])

        logger.info("claude_spawning",
            sender=sender_jid,
            session_id=session_id or "new",
            model=self.model,
            max_turns=self.max_turns)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=message.encode("utf-8")),
                timeout=self.timeout,
            )

            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")[:500]
                logger.error("claude_exit_error",
                    returncode=proc.returncode,
                    stderr=stderr_text,
                    sender=sender_jid)

                # If resume failed (stale session), retry without --resume
                if session_id and "session" in stderr_text.lower():
                    logger.info("claude_retrying_without_resume", sender=sender_jid)
                    self.clear_session(sender_jid)
                    return await self.generate_reply(sender_jid, message, sender_name)

                return "I'm having trouble processing your message right now. Please try again."

            stdout_text = stdout.decode("utf-8", errors="replace")
            result = json.loads(stdout_text)

            reply = result.get("result", "")
            new_session_id = result.get("session_id", "")

            if new_session_id:
                self._session_map[sender_jid] = new_session_id
                self._save_session_map()
                logger.info("claude_session_stored",
                    sender=sender_jid,
                    session_id=new_session_id)

            logger.info("claude_reply_generated",
                sender=sender_jid,
                reply_length=len(reply),
                session_id=new_session_id or session_id)

            return reply

        except asyncio.TimeoutError:
            logger.error("claude_timeout",
                sender=sender_jid,
                timeout=self.timeout)
            return "Sorry, I took too long thinking about that. Please try a simpler question."

        except json.JSONDecodeError as e:
            logger.error("claude_json_parse_error",
                error=str(e),
                sender=sender_jid,
                stdout_preview=stdout_text[:200] if 'stdout_text' in dir() else "N/A")
            return "I had trouble processing my response. Please try again."

        except Exception as e:
            logger.error("claude_unexpected_error",
                error=str(e),
                sender=sender_jid)
            return "Something went wrong. Please try again later."
