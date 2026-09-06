"""Answer coercion, re-exported for the hiring domain.

The implementation moved to :mod:`app.core.answers` when the sourcing app
arrived and needed exactly the same coercion. Hunar returns strings for
every field regardless of the declared type, which is a fact about the
provider rather than about screening, so it belongs in shared code.

This module stays as the hiring domain's name for it. That keeps the
call sites reading naturally and means the move cost no churn at the
places that actually use it.
"""

from __future__ import annotations

from app.core.answers import coerce_value, normalize_result

__all__ = ["coerce_value", "normalize_result"]
