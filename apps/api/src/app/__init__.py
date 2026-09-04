"""Backend for the AI Hiring Assistant and People Search & Reachout apps.

Layout follows one rule: ``hiring`` and ``people`` are independent domains
that may share only ``core`` and the ``hunar_sdk`` package. Neither may
import the other. That boundary is enforced by a lint rule rather than
convention, because the temptation to reach across grows once the two
start looking similar, and the moment they couple, either becomes
impossible to reason about alone.
"""

from __future__ import annotations

__version__ = "0.1.0"
