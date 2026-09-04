"""AI Hiring Assistant: screening job applicants by voice.

A recruiter defines a role and the questions they want asked, loads
candidates, and launches AI voice calls. Extracted answers land in a
dashboard with transparent scoring and a ranked shortlist.

This package must never import from ``app.people``. Anything both domains
need belongs in ``app.core`` or in the ``hunar_sdk`` package.
"""

from __future__ import annotations
