"""Cross-cutting concerns shared by both application domains.

Configuration, logging, the error envelope and pagination live here. Any
code either domain needs belongs in this package or in ``hunar_sdk``,
never in the sibling domain.
"""

from __future__ import annotations
