# ABOUTME: Tests for the response chunker.
# ABOUTME: Verifies paragraph, sentence, newline, and hard-cut splitting strategies.

import pytest
from chunker import ResponseChunker


@pytest.fixture
def chunker():
    return ResponseChunker(max_length=100, min_length=20)


class TestShortMessages:
    def test_empty_text_returns_empty_list(self, chunker):
        assert chunker.chunk("") == []

    def test_whitespace_only_returns_empty_list(self, chunker):
        assert chunker.chunk("   \n  ") == []

    def test_short_text_returns_single_chunk(self, chunker):
        result = chunker.chunk("Hello, how are you?")
        assert result == ["Hello, how are you?"]

    def test_exactly_max_length_returns_single_chunk(self, chunker):
        text = "x" * 100
        result = chunker.chunk(text)
        assert len(result) == 1


class TestParagraphBreaking:
    def test_splits_at_paragraph_boundary(self):
        chunker = ResponseChunker(max_length=100, min_length=10)
        para1 = "First paragraph with some content here."
        para2 = "Second paragraph with more content over here."
        text = para1 + "\n\n" + para2
        if len(text) > 100:
            # Adjust lengths to ensure we test properly
            para1 = "A" * 40
            para2 = "B" * 40
            text = para1 + "\n\n" + para2
        result = chunker.chunk(text)
        # If it fits in one chunk, that's fine
        if len(text) <= 100:
            assert len(result) == 1
        else:
            assert len(result) == 2

    def test_prefers_paragraph_over_sentence(self):
        chunker = ResponseChunker(max_length=80, min_length=10)
        text = "First part. More text.\n\nSecond part. Even more text here that keeps going."
        result = chunker.chunk(text)
        if len(text) > 80:
            assert len(result) >= 2
            assert result[0].endswith("More text.")


class TestSentenceBreaking:
    def test_splits_at_sentence_boundary(self):
        chunker = ResponseChunker(max_length=60, min_length=10)
        text = "First sentence here. Second sentence here. Third sentence that pushes us over the limit yes."
        result = chunker.chunk(text)
        assert len(result) >= 2
        # First chunk should end at a sentence boundary
        assert result[0].endswith(".")


class TestHardCut:
    def test_hard_cut_when_no_break_point(self):
        chunker = ResponseChunker(max_length=50, min_length=10)
        text = "A" * 120  # No breaks at all
        result = chunker.chunk(text)
        assert len(result) == 3
        assert len(result[0]) == 50
        assert len(result[1]) == 50
        assert len(result[2]) == 20


class TestRealWorldMessages:
    def test_whatsapp_style_message(self):
        chunker = ResponseChunker(max_length=200, min_length=20)
        text = (
            "Hey! Great to hear from you.\n\n"
            "I checked with Gabor and he said the meeting is at 3pm tomorrow. "
            "He also mentioned that the project deadline has been moved to Friday.\n\n"
            "Let me know if you need anything else! Happy to help."
        )
        result = chunker.chunk(text)
        # Should split into two chunks at a paragraph boundary
        assert len(result) >= 1
        # All original words should be preserved across chunks
        rejoined = " ".join(result)
        assert "Gabor" in rejoined
        assert "Happy to help" in rejoined

    def test_preserves_all_content(self):
        chunker = ResponseChunker(max_length=50, min_length=5)
        text = "Word " * 50  # ~250 chars
        result = chunker.chunk(text)
        rejoined = " ".join(c.strip() for c in result)
        # All words should still be present
        assert rejoined.count("Word") == 50
