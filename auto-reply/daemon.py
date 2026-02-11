# ABOUTME: Main entry point for the WhatsApp auto-reply daemon.
# ABOUTME: Receives webhook notifications from Go bridge, orchestrates reply pipeline.

import asyncio
import os
import time
from datetime import datetime
from collections import defaultdict
import structlog
from aiohttp import web

from config import load_config
from pairing import PairingStore, ContactStatus
from sessions import SessionManager, SessionMessage
from llm import LLMClient
from chunker import ResponseChunker
from bridge import BridgeClient

logger = structlog.get_logger("auto-reply")


class AutoReplyDaemon:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.app = web.Application()
        self.app.router.add_post("/webhook/message", self.handle_webhook)
        self.app.router.add_get("/health", self.handle_health)

        # Initialize components
        self.pairing = PairingStore(
            db_path="store/pairing.db",
            code_expiry_minutes=self.config.pairing.code_expiry_minutes,
            code_length=self.config.pairing.code_length
        )
        self.sessions = SessionManager(
            storage_dir=self.config.session.storage_dir,
            idle_reset_minutes=self.config.session.idle_reset_minutes,
            max_history_tokens=self.config.session.max_history_tokens,
            compaction_target_tokens=self.config.session.compaction_target_tokens
        )
        self.llm = LLMClient(
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
            persona_file=self.config.persona_file
        )
        self.chunker = ResponseChunker(
            max_length=self.config.security.max_message_length
        )
        self.bridge = BridgeClient(
            base_url=self.config.bridge.url,
            timeout=self.config.bridge.send_timeout
        )

        # Rate limiting: track last reply time per sender
        self._last_reply_time: dict[str, float] = defaultdict(float)

        # Processing lock per sender (sequential message processing)
        self._sender_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Webhook secret for authentication
        self._webhook_secret = os.environ.get("AUTOREPLY_WEBHOOK_SECRET", "")

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """Receive incoming message notification from Go bridge."""
        # Validate webhook secret if configured
        if self._webhook_secret:
            received_secret = request.headers.get("X-Webhook-Secret", "")
            if received_secret != self._webhook_secret:
                logger.warning("[SECURITY] Invalid webhook secret")
                return web.json_response({"status": "unauthorized"}, status=401)

        try:
            payload = await request.json()
            logger.info("webhook_received",
                message_id=payload.get("message_id"),
                sender=payload.get("sender_jid"),
                content_preview=payload.get("content", "")[:50])

            # Process message asynchronously
            asyncio.create_task(self.process_message(payload))
            return web.json_response({"status": "accepted"})

        except Exception as e:
            logger.error("webhook_error", error=str(e))
            return web.json_response({"status": "error", "detail": str(e)}, status=400)

    async def process_message(self, payload: dict):
        """Main message processing pipeline."""
        sender_jid = payload.get("sender_jid", "")
        content = payload.get("content", "")
        is_from_me = payload.get("is_from_me", False)
        is_group = payload.get("is_group", False)
        sender_name = payload.get("sender_name", "")
        timestamp = payload.get("timestamp", datetime.now().isoformat())
        media_type = payload.get("media_type", "")

        # ── Security checks ──────────────────────────────────
        if is_from_me:
            return

        if is_group and self.config.security.block_groups:
            logger.info("[SECURITY] Blocked group message", sender=sender_jid)
            return

        # ── Rate limiting ─────────────────────────────────────
        now = time.time()
        last = self._last_reply_time.get(sender_jid, 0)
        if now - last < self.config.security.rate_limit_seconds:
            logger.info("rate_limited", sender=sender_jid,
                wait=round(self.config.security.rate_limit_seconds - (now - last), 1))
            return

        # ── Sequential processing per sender ──────────────────
        async with self._sender_locks[sender_jid]:
            await self._process_message_locked(
                sender_jid, content, sender_name, timestamp, media_type
            )

    async def _process_message_locked(self, sender_jid: str, content: str,
                                       sender_name: str, timestamp: str,
                                       media_type: str):
        """Process a message with per-sender lock held."""

        # ── Pairing check ─────────────────────────────────────
        if self.config.pairing.enabled:
            status = self.pairing.check_access(sender_jid)

            if status == ContactStatus.BLOCKED:
                logger.info("[SECURITY] Blocked sender", sender=sender_jid)
                return

            if status == ContactStatus.UNKNOWN:
                code = self.pairing.generate_pairing_code(sender_jid, sender_name)
                pairing_msg = (
                    f"Hi! This is an automated assistant.\n\n"
                    f"To start chatting, you need approval.\n"
                    f"Your pairing code: {code}\n\n"
                    f"Please share this code with the account owner.\n"
                    f"This code expires in {self.config.pairing.code_expiry_minutes} minutes."
                )
                await self.bridge.send_message(sender_jid, pairing_msg)
                self._last_reply_time[sender_jid] = time.time()
                return

            if status == ContactStatus.PENDING:
                await self.bridge.send_message(sender_jid,
                    "Your pairing request is still pending approval. "
                    "Please wait for the account owner to approve your code."
                )
                self._last_reply_time[sender_jid] = time.time()
                return

        # ── Handle media messages ─────────────────────────────
        if media_type and not content:
            content = f"[Sent a {media_type} message]"
        elif media_type and content:
            content = f"[Sent a {media_type}] {content}"

        if not content:
            return

        # ── Session management ────────────────────────────────
        session_key = self.sessions.get_or_create_session(sender_jid, sender_name)

        # Add incoming message
        self.sessions.add_message(session_key, SessionMessage(
            role="user",
            content=content,
            timestamp=timestamp,
            sender_jid=sender_jid,
            sender_name=sender_name
        ))

        # Check compaction
        if self.sessions.needs_compaction(session_key):
            logger.info("session_needs_compaction", session_key=session_key)
            history = self.sessions.get_history_as_api_messages(session_key)
            summary = self.llm.generate_compaction_summary(history)
            self.sessions.compact_session(session_key, summary)

        # ── LLM call ──────────────────────────────────────────
        history = self.sessions.get_history_as_api_messages(session_key)
        reply = self.llm.generate_reply(history, sender_name)

        # ── Response delivery ─────────────────────────────────
        chunks = self.chunker.chunk(reply)
        results = await self.bridge.send_chunked(sender_jid, chunks)

        any_success = any(success for success, _ in results)

        if any_success:
            self.sessions.add_message(session_key, SessionMessage(
                role="assistant",
                content=reply,
                timestamp=datetime.now().isoformat()
            ))

        self._last_reply_time[sender_jid] = time.time()

        logger.info("reply_sent",
            sender=sender_jid,
            chunks=len(chunks),
            reply_length=len(reply),
            success=any_success)

    async def handle_health(self, request: web.Request) -> web.Response:
        bridge_ok = await self.bridge.health_check()
        return web.json_response({
            "status": "ok" if bridge_ok else "degraded",
            "version": "0.1.0",
            "bridge_connected": bridge_ok,
            "model": self.config.llm.model,
            "pairing_enabled": self.config.pairing.enabled,
            "active_sessions": len(self.sessions.get_all_sessions())
        })


def main():
    daemon = AutoReplyDaemon()
    config = daemon.config

    logger.info("starting_daemon",
        host=config.daemon.host,
        port=config.daemon.port,
        model=config.llm.model,
        pairing_enabled=config.pairing.enabled,
        allowed_recipients=config.security.allowed_recipients)

    web.run_app(
        daemon.app,
        host=config.daemon.host,
        port=config.daemon.port,
        print=lambda msg: logger.info("server_info", message=msg)
    )


if __name__ == "__main__":
    main()
