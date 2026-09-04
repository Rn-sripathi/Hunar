#!/usr/bin/env python
"""Write the API's OpenAPI schema to a file.

Feeds ``openapi-typescript``, so the frontend's types are generated from
the backend's actual contract rather than hand-copied from it. A field
renamed in a Pydantic model then becomes a TypeScript compile error
rather than a value that silently arrives as ``undefined``.

Runs without a database or a network: the schema is derived from the route
signatures, so a mock-mode application is enough to produce it.

    uv run python scripts/dump_openapi.py apps/web/openapi.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "hunar-sdk" / "src"))

# The schema depends only on route signatures, so a credential-free
# configuration is enough and keeps this runnable in CI.
os.environ.setdefault("HUNAR_MODE", "mock")
os.environ.setdefault("HUNAR_API_KEY", "not-used-for-schema-generation")

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> int:
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "apps/web/openapi.json")
    if not destination.is_absolute():
        destination = REPO_ROOT / destination

    app = create_app(Settings(hunar_mode="mock", hunar_api_key="schema-only"))
    schema = app.openapi()

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")

    paths = len(schema.get("paths", {}))
    models = len(schema.get("components", {}).get("schemas", {}))
    print(f"wrote {destination.relative_to(REPO_ROOT)}  ({paths} paths, {models} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
