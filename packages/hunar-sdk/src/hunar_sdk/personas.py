"""The name each Hunar voice should introduce itself with.

This lives in the SDK rather than in an application because it is a fact
about the provider's voices, not about hiring or sourcing. Both
applications need it, and putting it in either one would make the other
reach across a boundary it should not cross.

The name has to follow the voice rather than sit beside it. A hardcoded
default once meant a role using the SAM voice opened with "My name is
Neha" in a male voice, which a candidate hears in the first sentence and
which costs their trust in everything after it.
"""

from __future__ import annotations

from .sanitize import sanitize

__all__ = ["PERSONA_NAMES", "persona_name_for"]

PERSONA_NAMES: dict[str, str] = {
    "NEHA": "Neha",
    "ROY": "Roy",
    "ZOE": "Zoe",
    "SAM": "Sam",
    "MIRA": "Mira",
    "EESHA": "Eesha",
}

#: Used only if the provider adds a voice we have no name for. Neutral on
#: purpose: a wrong-sounding name is worse than a plain one.
_FALLBACK_PERSONA_NAME = "Priya"


def persona_name_for(voice_persona: str, override: str | None = None) -> str:
    """The name this agent should say, given its voice.

    An explicit override wins, so an operator can call the agent whatever
    the company wants. Otherwise the name is derived from the voice,
    which is the only way the two cannot drift apart.
    """
    if override and override.strip():
        return sanitize(override, max_length=60)
    return PERSONA_NAMES.get(voice_persona.upper(), _FALLBACK_PERSONA_NAME)
