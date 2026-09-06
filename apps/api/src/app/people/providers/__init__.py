"""People-search providers behind one interface.

Four providers are named in the brief and none of them will return a
mobile number on a free tier:

* **People Data Labs** self-serves a key in minutes and does real person
  search, but since v29.0 free plans return contact fields as ``true`` or
  ``false`` rather than values. You learn that a mobile exists. You do not
  learn what it is.
* **Apollo.io** excludes API access from its free plan entirely.
* **Proxycurl** shut down in July 2025 after the LinkedIn lawsuit.
* **Coresignal** holds no personal phone numbers at all, by design.

So the chain the brief describes, search to phone to call, cannot be
closed on free credits. Rather than pretend otherwise, the product splits
the two permissions: sourcing is real and unrestricted, calling is gated
on a consent allowlist. That is also the right answer where the data came
from a broker, so the constraint and the ethics point the same way.

The abstraction is one Protocol, one normalised model, and adapters. No
registry, no plugin loader. It exists so the provider choice is
reversible, not to be a framework.
"""

from __future__ import annotations

from app.people.providers.base import (
    PeopleSearchProvider,
    Prospect,
    SearchPage,
    dedupe_key_for,
)
from app.people.providers.fixture import FixtureProvider
from app.people.providers.pdl import PdlProvider

__all__ = [
    "FixtureProvider",
    "PdlProvider",
    "PeopleSearchProvider",
    "Prospect",
    "SearchPage",
    "build_provider",
    "dedupe_key_for",
]


def build_provider(name: str, api_key: str = "") -> PeopleSearchProvider:
    """Return the provider for a configured name.

    Falls back to fixtures rather than raising. A missing key should
    degrade the demo, not break the screen, and the response says which
    provider actually served it.
    """
    if name in {"pdl", "pdl_sandbox"} and api_key:
        return PdlProvider(api_key=api_key, sandbox=name == "pdl_sandbox")
    return FixtureProvider()
