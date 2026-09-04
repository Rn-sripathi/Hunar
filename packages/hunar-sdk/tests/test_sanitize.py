"""Tests for prompt text sanitising.

Hunar substitutes ``custom_data`` into prompts with single-brace
``{variable}`` syntax, so any brace in operator-supplied text is a live
delimiter. The failure mode is not a crash but a corrupted sentence spoken
aloud to a real candidate on a real phone call, which makes this worth
covering properly despite the small surface area.
"""

from __future__ import annotations

import pytest

from hunar_sdk.sanitize import sanitize, sanitize_custom_data, strip_braces


class TestStripBraces:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain text", "plain text"),
            ("{candidate_name}", "candidate_name"),
            ("salary {35-50 LPA}", "salary 35-50 LPA"),
            ('config: {"a": 1}', 'config: "a": 1'),
            ("}}}{{{", ""),
            ("", ""),
        ],
    )
    def test_removes_every_brace(self, raw: str, expected: str) -> None:
        assert strip_braces(raw) == expected

    def test_handles_none(self) -> None:
        assert strip_braces(None) == ""

    def test_leaves_no_brace_behind(self) -> None:
        """The property that actually matters, stated directly."""
        messy = 'JD: use {framework} with {"opts": {"x": [1,2]}} and earn {50} LPA'
        assert "{" not in strip_braces(messy)
        assert "}" not in strip_braces(messy)


class TestSanitize:
    def test_collapses_runaway_whitespace(self) -> None:
        assert sanitize("too      many   spaces") == "too many spaces"

    def test_collapses_excess_blank_lines(self) -> None:
        assert sanitize("a\n\n\n\n\nb") == "a\n\nb"

    def test_normalises_windows_line_endings(self) -> None:
        """Job descriptions pasted on Windows arrive with CRLF."""
        assert sanitize("line one\r\nline two") == "line one\nline two"

    def test_strips_trailing_whitespace_per_line(self) -> None:
        assert sanitize("alpha   \nbeta  ") == "alpha\nbeta"

    def test_truncates_on_a_word_boundary(self) -> None:
        result = sanitize("the quick brown fox jumps over", max_length=15)
        assert result == "the quick…"
        assert "brow" not in result

    def test_does_not_truncate_when_within_limit(self) -> None:
        assert sanitize("short", max_length=100) == "short"

    def test_strips_braces_as_well(self) -> None:
        assert "{" not in sanitize("pay is {40 LPA}")

    def test_handles_none_and_empty(self) -> None:
        assert sanitize(None) == ""
        assert sanitize("") == ""

    def test_preserves_devanagari(self) -> None:
        """Screening runs in Indian languages, so text must survive intact."""
        assert sanitize("नमस्ते, आप कैसे हैं?") == "नमस्ते, आप कैसे हैं?"


class TestSanitizeCustomData:
    def test_stringifies_scalars(self) -> None:
        """Hunar accepts string values only."""
        result = sanitize_custom_data({"years": 5, "rate": 4.5})
        assert result == {"years": "5", "rate": "4.5"}

    def test_lowercases_booleans(self) -> None:
        """`str(True)` would send "True"; prompts read better lowercased."""
        result = sanitize_custom_data({"open": True, "remote": False})
        assert result == {"open": "true", "remote": "false"}

    def test_drops_none_values(self) -> None:
        """A missing value must be absent, never the literal text "None"."""
        result = sanitize_custom_data({"present": "yes", "absent": None})
        assert result == {"present": "yes"}
        assert "absent" not in result

    def test_strips_braces_from_keys_and_values(self) -> None:
        result = sanitize_custom_data({"a{b}": "pay {40}"})
        assert result == {"ab": "pay 40"}

    def test_every_value_is_a_string(self) -> None:
        result = sanitize_custom_data({"a": 1, "b": True, "c": "x", "d": 2.5, "e": None})
        assert all(isinstance(value, str) for value in result.values())
