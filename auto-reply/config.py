# ABOUTME: Configuration models for the WhatsApp auto-reply daemon.
# ABOUTME: Loads from config.yaml, validates with Pydantic v2, supports env var overrides.

from pydantic import BaseModel
from pathlib import Path
import yaml
import os


class BridgeConfig(BaseModel):
    """Go bridge connection settings."""
    url: str = "http://localhost:8082/api"
    send_timeout: int = 10


class DaemonConfig(BaseModel):
    """Daemon HTTP server settings."""
    host: str = "127.0.0.1"
    port: int = 8084


class ClaudeConfig(BaseModel):
    """Claude Code CLI settings."""
    model: str = "claude-sonnet-4-5-20250929"
    max_turns: int = 5
    timeout: int = 120
    workspace_dir: str = "workspace"
    mcp_config: str = ""


class SessionConfig(BaseModel):
    """Session management settings."""
    idle_reset_minutes: int = 60
    storage_dir: str = "sessions"


class PairingConfig(BaseModel):
    """Access control settings."""
    enabled: bool = True
    code_expiry_minutes: int = 10
    code_length: int = 6


class SecurityConfig(BaseModel):
    """Security constraints."""
    allowed_recipients: list[str] = []
    block_groups: bool = True
    rate_limit_seconds: float = 5.0
    max_message_length: int = 4096


class AutoReplyConfig(BaseModel):
    """Root configuration model."""
    bridge: BridgeConfig = BridgeConfig()
    daemon: DaemonConfig = DaemonConfig()
    claude: ClaudeConfig = ClaudeConfig()
    session: SessionConfig = SessionConfig()
    pairing: PairingConfig = PairingConfig()
    security: SecurityConfig = SecurityConfig()
    persona_file: str = "PERSONA.md"


def load_config(config_path: str = "config.yaml") -> AutoReplyConfig:
    """Load config from YAML file with env var overrides."""
    config_data = {}
    if Path(config_path).exists():
        with open(config_path) as f:
            config_data = yaml.safe_load(f) or {}

    config = AutoReplyConfig(**config_data)

    # Override allowed recipients from env
    allowed_raw = os.environ.get("WHATSAPP_MCP_ALLOWED_RECIPIENT", "")
    if allowed_raw:
        config.security.allowed_recipients = [
            jid.strip() for jid in allowed_raw.split(",") if jid.strip()
        ]

    return config
