# ABOUTME: Session manager for per-sender conversation history.
# ABOUTME: Stores JSONL transcripts, handles idle reset and context compaction.

import json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
import structlog

logger = structlog.get_logger("sessions")


@dataclass
class SessionMessage:
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: str
    sender_jid: Optional[str] = None
    sender_name: Optional[str] = None
    type: Optional[str] = None  # "compaction", "reset", None for normal


@dataclass
class SessionMetadata:
    session_key: str
    last_activity: str
    message_count: int = 0
    estimated_tokens: int = 0
    created_at: str = ""
    sender_name: str = ""


class SessionManager:
    def __init__(self, storage_dir: str = "sessions",
                 idle_reset_minutes: int = 60,
                 max_history_tokens: int = 50000,
                 compaction_target_tokens: int = 10000):
        self.storage_dir = Path(storage_dir)
        self.idle_reset_minutes = idle_reset_minutes
        self.max_history_tokens = max_history_tokens
        self.compaction_target_tokens = compaction_target_tokens

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._metadata: dict[str, SessionMetadata] = {}
        self._load_metadata()

    def _metadata_path(self) -> Path:
        return self.storage_dir / "metadata.json"

    def _session_path(self, session_key: str) -> Path:
        safe_name = session_key.replace(":", "_").replace("/", "_")
        return self.storage_dir / f"{safe_name}.jsonl"

    def _load_metadata(self):
        path = self._metadata_path()
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                self._metadata = {
                    k: SessionMetadata(**v) for k, v in data.items()
                }

    def _save_metadata(self):
        with open(self._metadata_path(), "w") as f:
            json.dump({k: asdict(v) for k, v in self._metadata.items()}, f, indent=2)

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def session_key_for_jid(self, jid: str) -> str:
        return f"whatsapp:{jid}"

    def get_or_create_session(self, jid: str, sender_name: str = "") -> str:
        key = self.session_key_for_jid(jid)

        if key in self._metadata:
            meta = self._metadata[key]
            last = datetime.fromisoformat(meta.last_activity)
            if datetime.now() - last > timedelta(minutes=self.idle_reset_minutes):
                logger.info("session_idle_reset", session_key=key,
                    idle_minutes=(datetime.now() - last).total_seconds() / 60)
                self.reset_session(key, reason="idle timeout")
                return self._create_session(key, sender_name)
            return key
        else:
            return self._create_session(key, sender_name)

    def _create_session(self, key: str, sender_name: str = "") -> str:
        now = datetime.now().isoformat()
        self._metadata[key] = SessionMetadata(
            session_key=key,
            last_activity=now,
            created_at=now,
            sender_name=sender_name
        )
        self._save_metadata()
        logger.info("session_created", session_key=key, sender_name=sender_name)
        return key

    def add_message(self, session_key: str, message: SessionMessage):
        path = self._session_path(session_key)
        with open(path, "a") as f:
            f.write(json.dumps(asdict(message)) + "\n")

        if session_key in self._metadata:
            meta = self._metadata[session_key]
            meta.message_count += 1
            meta.estimated_tokens += self._estimate_tokens(message.content)
            meta.last_activity = message.timestamp
            if message.sender_name:
                meta.sender_name = message.sender_name
            self._save_metadata()

    def get_history(self, session_key: str) -> list[SessionMessage]:
        path = self._session_path(session_key)
        if not path.exists():
            return []

        messages = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    messages.append(SessionMessage(**data))
        return messages

    def get_history_as_api_messages(self, session_key: str) -> list[dict]:
        history = self.get_history(session_key)
        messages = []
        for msg in history:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})
            elif msg.role == "system" and msg.type == "compaction":
                messages.append({"role": "user", "content": f"[Previous conversation summary: {msg.content}]"})
        return messages

    def needs_compaction(self, session_key: str) -> bool:
        if session_key not in self._metadata:
            return False
        return self._metadata[session_key].estimated_tokens > self.max_history_tokens

    def compact_session(self, session_key: str, summary: str):
        path = self._session_path(session_key)
        now = datetime.now().isoformat()

        compaction_msg = SessionMessage(
            role="system",
            content=summary,
            timestamp=now,
            type="compaction"
        )

        with open(path, "w") as f:
            f.write(json.dumps(asdict(compaction_msg)) + "\n")

        if session_key in self._metadata:
            meta = self._metadata[session_key]
            meta.message_count = 1
            meta.estimated_tokens = self._estimate_tokens(summary)
            self._save_metadata()

        logger.info("session_compacted", session_key=session_key,
            summary_tokens=self._estimate_tokens(summary))

    def reset_session(self, session_key: str, reason: str = "manual"):
        path = self._session_path(session_key)
        if path.exists():
            now = datetime.now().isoformat()
            reset_msg = SessionMessage(
                role="system",
                content=f"Session reset: {reason}",
                timestamp=now,
                type="reset"
            )
            with open(path, "w") as f:
                f.write(json.dumps(asdict(reset_msg)) + "\n")

        if session_key in self._metadata:
            del self._metadata[session_key]
            self._save_metadata()

        logger.info("session_reset", session_key=session_key, reason=reason)

    def get_all_sessions(self) -> list[SessionMetadata]:
        return list(self._metadata.values())
