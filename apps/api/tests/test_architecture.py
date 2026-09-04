"""Architectural boundaries, enforced rather than documented.

The rule this file protects is simple and easy to break by accident: the
``hiring`` and ``people`` domains must stay independent. Both place voice
calls, both show a results table, and both will grow similar-looking
code, so the temptation to reach across is constant. The moment one
imports the other, neither can be understood, tested or changed alone.

Anything genuinely shared belongs in ``app.core`` or in the ``hunar_sdk``
package. A linter cannot express this rule, because it is directional:
``hiring`` importing itself is fine and ``hiring`` importing ``people`` is
not, and a global import ban cannot tell the two apart.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "app"


def imported_modules(path: Path) -> set[str]:
    """Every module name imported by one file, absolute and relative."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def python_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


def _offenders(package: str, forbidden_prefix: str) -> list[str]:
    problems: list[str] = []
    for path in python_files(package):
        for module in imported_modules(path):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                problems.append(f"{path.relative_to(SRC)} imports {module}")
    return problems


class TestDomainIndependence:
    def test_hiring_does_not_import_people(self) -> None:
        offenders = _offenders("hiring", "app.people")
        assert not offenders, (
            "The hiring domain must not depend on the people domain. "
            "Move the shared piece into app.core or hunar_sdk.\n" + "\n".join(offenders)
        )

    def test_people_does_not_import_hiring(self) -> None:
        if not (SRC / "people").exists():
            pytest.skip("the people domain does not exist yet")
        offenders = _offenders("people", "app.hiring")
        assert not offenders, (
            "The people domain must not depend on the hiring domain. "
            "Move the shared piece into app.core or hunar_sdk.\n" + "\n".join(offenders)
        )

    def test_core_does_not_import_either_domain(self) -> None:
        """``core`` is shared, so depending on a domain inverts the layering.

        It would also mean adding the second application requires editing
        the first one's foundations, which is exactly what this structure
        exists to avoid.
        """
        offenders = _offenders("core", "app.hiring") + _offenders("core", "app.people")
        assert not offenders, (
            "app.core is shared by both domains and must not depend on either.\n"
            + "\n".join(offenders)
        )


class TestSdkIndependence:
    def test_the_sdk_knows_nothing_about_this_application(self) -> None:
        """``hunar_sdk`` describes Hunar's API and nothing else.

        Keeping it free of application imports is what lets it be reused
        by the second app, and tested without a database or a web server.
        """
        sdk = Path(__file__).resolve().parents[3] / "packages" / "hunar-sdk" / "src" / "hunar_sdk"
        problems = [
            f"{path.name} imports {module}"
            for path in sorted(sdk.rglob("*.py"))
            for module in imported_modules(path)
            if module == "app" or module.startswith("app.")
        ]
        assert not problems, "\n".join(problems)


class TestWebhookSafety:
    def test_the_webhook_route_reads_the_raw_body(self) -> None:
        """The HMAC covers the exact bytes received.

        Parsing JSON before verifying, or letting middleware rewrite the
        body, invalidates every signature. That failure is silent and
        looks like Hunar sending bad signatures, so it is pinned here.
        """
        source = (SRC / "webhooks" / "router.py").read_text(encoding="utf-8")
        body_read = source.index("await request.body()")
        verification = source.index("verify_webhook(")
        parse = source.index("json.loads(")

        assert body_read < verification, "the body must be read before verification"
        assert verification < parse, "the signature must be verified before parsing"

    def test_no_body_rewriting_middleware_is_registered(self) -> None:
        """Compression or body-rewriting middleware would break the HMAC."""
        main = (SRC / "main.py").read_text(encoding="utf-8")
        for banned in ("GZipMiddleware", "BrotliMiddleware"):
            assert banned not in main, (
                f"{banned} rewrites request bodies and would invalidate "
                "every inbound webhook signature"
            )
