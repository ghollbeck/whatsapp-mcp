# ABOUTME: Anthropic API client for generating WhatsApp auto-replies.
# ABOUTME: Loads persona from PERSONA.md, supports hot-reload and context compaction.

import anthropic
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger("llm")


class LLMClient:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929",
                 max_tokens: int = 1024, temperature: float = 0.7,
                 persona_file: str = "PERSONA.md"):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.persona_file = persona_file
        self._persona_cache: Optional[str] = None
        self._persona_mtime: float = 0

    def load_persona(self) -> str:
        path = Path(self.persona_file)
        if not path.exists():
            logger.warning("persona_file_missing", path=self.persona_file)
            return "You are a helpful WhatsApp assistant."

        mtime = path.stat().st_mtime
        if self._persona_cache is None or mtime > self._persona_mtime:
            with open(path) as f:
                self._persona_cache = f.read().strip()
            self._persona_mtime = mtime
            logger.info("persona_loaded", path=self.persona_file, length=len(self._persona_cache))

        return self._persona_cache

    def generate_reply(self, messages: list[dict], sender_name: str = "") -> str:
        persona = self.load_persona()

        system_prompt = persona
        if sender_name:
            system_prompt += f"\n\nYou are currently chatting with {sender_name} on WhatsApp."

        system_prompt += (
            "\n\nKeep responses conversational and concise — this is WhatsApp, not email. "
            "Avoid markdown formatting (no **, ##, etc.) since WhatsApp doesn't render it well."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=messages
            )

            reply = response.content[0].text
            logger.info("llm_reply_generated",
                model=self.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens)

            return reply

        except anthropic.RateLimitError as e:
            logger.error("llm_rate_limited", error=str(e))
            return "I'm receiving too many messages right now. Please try again in a moment."

        except anthropic.APIError as e:
            logger.error("llm_api_error", error=str(e))
            return "I'm having trouble processing your message. Please try again."

        except Exception as e:
            logger.error("llm_unexpected_error", error=str(e))
            return "Something went wrong. Please try again later."

    def generate_compaction_summary(self, messages: list[dict]) -> str:
        summary_prompt = [
            {
                "role": "user",
                "content": (
                    "Summarize the following conversation concisely. "
                    "Capture the key topics discussed, any decisions made, "
                    "important facts shared, and the overall tone. "
                    "This summary will be used as context for continuing "
                    "the conversation later.\n\n"
                    "Conversation:\n" +
                    "\n".join(f"{m['role']}: {m['content']}" for m in messages)
                )
            }
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                temperature=0.3,
                system="You are a conversation summarizer. Be concise and factual.",
                messages=summary_prompt
            )

            summary = response.content[0].text
            logger.info("compaction_summary_generated",
                input_messages=len(messages),
                summary_length=len(summary))
            return summary

        except Exception as e:
            logger.error("compaction_summary_error", error=str(e))
            return "Previous conversation context was lost due to an error."
