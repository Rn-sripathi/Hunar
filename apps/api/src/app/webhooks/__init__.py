"""Inbound webhook handling, shared by both application domains.

Correlation is by opaque token in the URL path rather than by anything in
the payload, because the ``call_result_done`` body carries no call
identifier. Since callback URLs are supplied per call, embedding a token
costs nothing and makes correlation independent of a payload shape the
documentation does not pin down.
"""

from __future__ import annotations
