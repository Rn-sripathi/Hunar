"""Business logic for the hiring domain.

Routers stay thin and delegate here. Anything that could be reasoned
about without a database or an HTTP request lives in a pure function, so
the parts most likely to be wrong are the parts easiest to test.
"""

from __future__ import annotations
