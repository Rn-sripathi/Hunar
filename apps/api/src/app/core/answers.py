"""What an extracted answer is, and how to make it usable.

This is shared rather than owned by either application, because both ask
a voice agent questions and both get the same thing back: a flat map of
strings, whatever types the schema declared. A field declared
``"boolean"`` comes back as ``"Yes"``, ``"true"``, ``"Haan"`` or ``"yes
sir"``. A field declared ``"number"`` comes back as ``"3"``, ``"3
years"`` or ``"around three"``. Nothing can sort, filter or score those
without a coercion step, so this module is required rather than
convenient.

Three principles govern it:

* **Never discard the original.** The raw string is stored alongside the
  coerced value and shown in the UI, so a person can always read what was
  actually said. A coercion bug then costs a misread column, not a lost
  answer.
* **Unparseable is not false.** A value this module cannot understand
  becomes ``None``, never ``False`` or ``0``. Treating "I'm not sure" as
  a no would silently reject someone on a parsing failure.
* **Answer in the speaker's language.** These calls run in Hindi, Tamil
  and mixed speech, so affirmatives and negatives are recognised across
  the languages this product actually serves.

It lives in ``core`` because moving it into either domain would force the
other to import across a boundary that exists to keep them independent.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

__all__ = [
    "AnswerType",
    "FieldSpec",
    "coerce_value",
    "mask_number",
    "normalize_result",
]


class AnswerType(StrEnum):
    """How an answer is coerced, rendered and scored.

    Hunar returns every value as a string, so this is our declaration of
    what the string is meant to represent. It drives the normaliser, the
    column renderer and the scoring rules from one place.
    """

    BOOLEAN = "BOOLEAN"
    NUMBER = "NUMBER"
    STRING = "STRING"
    ENUM = "ENUM"


class FieldSpec(BaseModel):
    """One column of a results table.

    Returned alongside the rows so the frontend can build a table for a
    schema it has never seen. That is what lets one table component serve
    screening answers and outreach answers without knowing which it is
    looking at. ``system`` marks fields that are always present regardless
    of what the operator asked for.
    """

    key: str
    label: str
    answer_type: AnswerType
    enum_options: list[str] | None = None
    weight: float = 0.0
    is_knockout: bool = False
    system: bool = False


def mask_number(number: str) -> str:
    """Mask a phone number for display, keeping the last four digits.

    People need enough to recognise a row; nobody needs the whole number
    rendered in a browser or captured in a screenshot.
    """
    digits = "".join(character for character in number if character.isdigit())
    if len(digits) <= 4:
        return "•" * len(digits)
    return f"{number[:3]}•••••{digits[-4:]}"


#: Affirmatives across the languages Hunar supports for Indian frontline
#: hiring, plus the transliterations people actually type and speak.
_TRUE_TOKENS = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "yup",
        "true",
        "1",
        "ok",
        "okay",
        "sure",
        "correct",
        "right",
        "affirmative",
        "definitely",
        "absolutely",
        "haan",
        "haa",
        "ha",
        "han",
        "haanji",
        "ji",
        "jee",
        "sahi",
        "theek",
        "aahan",
        "aama",
        "ama",
        "aam",
        "avunu",
        "howdu",
        "haudu",
        "athe",
        "हाँ",
        "हां",
        "जी",
        "सही",
        "ठीक",
        "ஆம்",
        "சரி",
        "అవును",
        "ಹೌದು",
        "അതെ",
        "হ্যাঁ",
    }
)

_FALSE_TOKENS = frozenset(
    {
        # "not" is deliberately absent. It is a negation particle, not an
        # answer, and treating it as one turns "I'm not sure" into a firm
        # refusal, which would reject a candidate for hesitating.
        "no",
        "n",
        "nope",
        "nah",
        "false",
        "0",
        "never",
        "negative",
        "nahi",
        "nahin",
        "naa",
        "na",
        "nai",
        "illa",
        "illai",
        "ledu",
        "नहीं",
        "ना",
        "नही",
        "இல்லை",
        "லேது",
        "లేదు",
        "ಇಲ್ಲ",
        "ഇല്ല",
        "না",
    }
)

#: Phrases that state uncertainty outright. Checked before anything else,
#: because most of them contain a token that would otherwise be read as a
#: definite yes or no. An unsure candidate is unknown, never a refusal.
_UNCERTAIN_PATTERNS = (
    "not sure",
    "no idea",
    "don't know",
    "dont know",
    "do not know",
    "cannot say",
    "can't say",
    "cant say",
    "not decided",
    "haven't decided",
    "depends",
    "maybe",
    "perhaps",
    "possibly",
    "might be",
    "pata nahi",
    "nahi pata",
    "malum nahi",
    "theek se nahi",
    "confused",
    "let me think",
    "will let you know",
    "tell you later",
)

#: Values that mean "we do not know", which must become null rather than
#: a default. Getting this wrong turns a silence into a rejection.
_NULL_TOKENS = frozenset(
    {
        "",
        "-",
        "--",
        "n/a",
        "na",
        "nil",
        "none",
        "null",
        "unknown",
        "unclear",
        "not answered",
        "not discussed",
        "not mentioned",
        "not provided",
        "not available",
        "unavailable",
        "not specified",
        "not stated",
        "no answer",
        "declined",
        "prefer not to say",
        "did not say",
        "no response",
    }
)

_WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "half": 0.5,
    "immediate": 0,
    "immediately": 0,
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "paanch": 5,
    "panch": 5,
    "chhe": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
}

_NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
_LAKH_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:lpa|lakh|lac|l\b)", re.IGNORECASE)
_CRORE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:cr|crore)", re.IGNORECASE)

_TOKEN_SPLIT = re.compile(r"[^\wऀ-෿]+")


def _clean(raw: Any) -> str | None:
    """Reduce a raw value to trimmed text, or ``None`` if it means nothing."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text.lower() in _NULL_TOKENS:
        return None
    return text or None


def _to_bool(text: str) -> bool | None:
    """Interpret an affirmative or negative, or give up honestly."""
    lowered = text.lower().strip(" .!?,")

    # Uncertainty first. "I'm not sure" and "pata nahi" both contain a
    # token that would otherwise read as a definite answer, and reporting
    # hesitation as refusal is the one error here with a human cost.
    if any(pattern in lowered for pattern in _UNCERTAIN_PATTERNS):
        return None

    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False

    # Phrases such as "yes I am", "nahi sir", "no, not right now".
    tokens = [token for token in _TOKEN_SPLIT.split(lowered) if token]
    if not tokens:
        return None

    # A negation anywhere outweighs an affirmative, because "yes but no"
    # and "haan nahi" both mean no in practice.
    if any(token in _FALSE_TOKENS for token in tokens):
        return False
    if any(token in _TRUE_TOKENS for token in tokens):
        return True

    return None


def _to_number(text: str) -> float | None:
    """Pull a number out of a spoken answer.

    Handles the units Indian candidates actually use for pay, because
    "12 LPA" and "1200000" are the same answer and a results table that
    sorts them differently is worse than useless.
    """
    lowered = text.lower().replace(",", "")

    if (crore := _CRORE_PATTERN.search(lowered)) is not None:
        return float(crore.group(1)) * 10_000_000
    if (lakh := _LAKH_PATTERN.search(lowered)) is not None:
        return float(lakh.group(1)) * 100_000

    if (match := _NUMBER_PATTERN.search(lowered)) is not None:
        return float(match.group())

    for word, value in _WORD_NUMBERS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return float(value)

    return None


def _to_enum(text: str, options: list[str] | None) -> str | None:
    """Snap an answer onto one of the allowed options.

    Falls back to the raw text rather than ``None`` when nothing matches,
    so an unexpected but real answer is still visible to the recruiter
    instead of vanishing from the table.
    """
    if not options:
        return text

    lowered = text.lower().strip()
    for option in options:
        if option.lower() == lowered:
            return option
    for option in options:
        if option.lower() in lowered or lowered in option.lower():
            return option
    return text


def coerce_value(raw: Any, answer_type: AnswerType, enum_options: list[str] | None = None) -> Any:
    """Coerce one extracted answer, returning ``None`` when unparseable."""
    text = _clean(raw)
    if text is None:
        return None

    match answer_type:
        case AnswerType.BOOLEAN:
            return _to_bool(text)
        case AnswerType.NUMBER:
            return _to_number(text)
        case AnswerType.ENUM:
            return _to_enum(text, enum_options)
        case _:
            return text


def normalize_result(
    raw_result: dict[str, Any] | None, field_spec: list[FieldSpec]
) -> dict[str, Any]:
    """Coerce a whole result payload against a field specification.

    Only declared fields are returned. An extra key Hunar invents is
    dropped rather than rendered, because the table's columns come from
    the same specification and a stray field would have nowhere to go.
    """
    if not raw_result:
        return {}

    return {
        field.key: coerce_value(raw_result.get(field.key), field.answer_type, field.enum_options)
        for field in field_spec
    }
