"""People Search & Reachout: sourcing candidates and calling them.

Paste a job description, find matching people through a people-search
provider, and reach out by voice. Answers land in the same dashboard
machinery the hiring app uses.

One principle governs this whole package, and it is worth stating before
any code: **sourcing is broad, calling is narrow.**

Prospects are sourced, ranked and displayed for real. Outbound dialling
goes only to numbers on a consent allowlist the operator controls, and
that restriction is enforced by a database constraint rather than by
convention. Three independent reasons:

* India's TCCCPR rules require commercial voice callers to be registered
  senders. This application is not one.
* People Data Labs' acceptable use policy forbids using their data for
  employment eligibility decisions, so a sourced record may inform
  outreach but must never gate a hiring decision.
* Cold-calling a stranger whose number came from a data broker is a thing
  you should have to opt into deliberately, not a default.

A convenient side effect of the provider landscape makes this easy to
honour: no provider in the brief returns a mobile number on a free tier
anyway. See ``docs/people-search-findings.md``.

This package must never import from ``app.hiring``.
"""

from __future__ import annotations
