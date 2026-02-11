# ABOUTME: Splits long LLM responses into WhatsApp-friendly message chunks.
# ABOUTME: Prefers paragraph breaks, then sentence breaks, then hard cuts.

import re
import structlog

logger = structlog.get_logger("chunker")


class ResponseChunker:
    def __init__(self, max_length: int = 4096, min_length: int = 100):
        self.max_length = max_length
        self.min_length = min_length

    def chunk(self, text: str) -> list[str]:
        text = text.strip()

        if not text:
            return []

        if len(text) <= self.max_length:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= self.max_length:
                chunks.append(remaining.strip())
                break

            split_pos = self._find_paragraph_break(remaining)

            if split_pos is None:
                split_pos = self._find_sentence_break(remaining)

            if split_pos is None:
                split_pos = self._find_newline_break(remaining)

            if split_pos is None:
                split_pos = self.max_length

            chunk = remaining[:split_pos].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_pos:].strip()

        logger.info("text_chunked", original_length=len(text), chunks=len(chunks))
        return chunks

    def _find_paragraph_break(self, text: str) -> int | None:
        search_area = text[:self.max_length]
        pos = search_area.rfind("\n\n")
        if pos > self.min_length:
            return pos + 2
        return None

    def _find_sentence_break(self, text: str) -> int | None:
        search_area = text[:self.max_length]
        matches = list(re.finditer(r'[.!?]\s', search_area))
        if matches:
            last_match = matches[-1]
            pos = last_match.end()
            if pos > self.min_length:
                return pos
        return None

    def _find_newline_break(self, text: str) -> int | None:
        search_area = text[:self.max_length]
        pos = search_area.rfind("\n")
        if pos > self.min_length:
            return pos + 1
        return None
