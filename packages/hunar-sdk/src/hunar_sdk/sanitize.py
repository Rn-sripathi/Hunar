"""Text sanitising for Hunar prompt templates.

Hunar substitutes ``custom_data`` into prompts using SINGLE-BRACE syntax,
``{variable_name}``. That makes every brace in operator-supplied text a
live template delimiter.

Recruiters paste job descriptions containing braces constantly, usually
from JSON snippets, code samples in engineering roles, or salary notes
like ``{35-50 LPA}``. Left alone, a stray brace either corrupts the
substitution or interpolates something unintended into what the agent
says out loud on a real phone call. So every string that reaches
``agent_prompt``, ``introduction``, ``objective``, ``result_prompt`` or a
``custom_data`` value goes through :func:`sanitize` first.

This lives in the SDK rather than in either app because both apps feed
operator text into prompts and the failure is silent in both.
"""

from __future__ import annotations

import re

__all__ = ["ALLOWED_PROMPT_VARIABLES", "sanitize", "sanitize_custom_data", "strip_braces"]

_BRACES = re.compile(r"[{}]")
_EXCESS_WHITESPACE = re.compile(r"[ \t]{2,}")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")

#: The only variables our prompt templates ever declare. Anything else
#: appearing in braces is operator text that leaked past sanitising.
ALLOWED_PROMPT_VARIABLES: frozenset[str] = frozenset(
    {
        "candidate_name",
        "callee_name",
        "job_title",
        "company_name",
        "job_city",
        "work_mode",
        "role_pitch",
        "comp_range_text",
        "recruiter_name",
        "persona_name",
        "callback_window_readback",
    }
)


def strip_braces(text: str | None) -> str:
    """Remove every brace so the text cannot act as a template delimiter."""
    if not text:
        return ""
    return _BRACES.sub("", text)


def sanitize(text: str | None, *, max_length: int | None = None) -> str:
    """Make operator-supplied text safe to embed in a Hunar prompt.

    Strips braces, normalises runaway whitespace that would waste prompt
    budget, and optionally truncates on a word boundary.
    """
    cleaned = strip_braces(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _EXCESS_WHITESPACE.sub(" ", cleaned)
    cleaned = _EXCESS_NEWLINES.sub("\n\n", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()

    if max_length is not None and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rsplit(" ", 1)[0].rstrip() + "…"
    return cleaned


def sanitize_custom_data(data: dict[str, object]) -> dict[str, str]:
    """Coerce a ``custom_data`` mapping to the shape Hunar accepts.

    Hunar requires string values only. Booleans and numbers arriving from
    our own models are stringified rather than rejected, ``None`` keys are
    dropped so a missing value renders as an absent variable instead of
    the literal text ``None``, and every value is brace-stripped.
    """
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        rendered = "true" if value is True else "false" if value is False else str(value)
        out[strip_braces(key).strip()] = sanitize(rendered, max_length=2000)
    return out
