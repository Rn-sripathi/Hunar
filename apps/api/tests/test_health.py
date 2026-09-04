"""Tests for the system endpoints.

``/meta`` gets the most attention because the frontend's honesty depends
on it. If it reported ``live`` while the demo client was serving
simulated calls, the dashboard would present invented conversations as
real ones, which is the single most misleading thing this application
could do.
"""

from __future__ import annotations

import httpx

from app import __version__


class TestLiveness:
    async def test_healthz_reports_ok(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__

    async def test_healthz_does_not_depend_on_the_database(self, client: httpx.AsyncClient) -> None:
        """Liveness must not flap when an upstream is briefly unavailable.

        No database is running in tests, so a passing call here proves
        liveness is genuinely shallow.
        """
        assert (await client.get("/healthz")).status_code == 200


class TestReadiness:
    async def test_reports_the_database_as_unreachable(self, client: httpx.AsyncClient) -> None:
        """With no database running, readiness should fail honestly."""
        response = await client.get("/readyz")
        assert response.status_code == 503

        body = response.json()
        assert body["ready"] is False
        database = next(d for d in body["dependencies"] if d["name"] == "database")
        assert database["ok"] is False

    async def test_treats_demo_mode_as_healthy(self, client: httpx.AsyncClient) -> None:
        """Demo mode is a working state, not a broken one.

        A deliberately degraded deploy must not look like an outage, or a
        platform health check would restart a perfectly functional service.
        """
        body = (await client.get("/readyz")).json()
        hunar = next(d for d in body["dependencies"] if d["name"] == "hunar")
        assert hunar["ok"] is True
        assert "mock" in (hunar["detail"] or "")


class TestMeta:
    async def test_reports_demo_mode_truthfully(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/meta")
        assert response.status_code == 200

        voice = response.json()["voice"]
        assert voice["mode"] == "mock"
        assert voice["configured_mode"] == "mock"

    async def test_supplies_a_banner_whenever_data_is_simulated(
        self, client: httpx.AsyncClient
    ) -> None:
        """The UI must never show simulated calls without saying so."""
        voice = (await client.get("/meta")).json()["voice"]
        assert voice["banner"], "demo mode produced no banner text"
        assert "simulated" in voice["banner"].lower()

    async def test_distinguishes_configured_from_effective_mode(
        self, client: httpx.AsyncClient
    ) -> None:
        """`auto` that has fallen back is a different story from `mock`.

        Keeping the two separate is what lets the banner explain *why* the
        data is simulated rather than merely that it is.
        """
        body = (await client.get("/meta")).json()
        assert set(body["voice"]) >= {
            "mode",
            "configured_mode",
            "degraded",
            "degrade_reason",
        }
        assert body["voice"]["degraded"] is False

    async def test_publishes_the_allowlist_size_but_no_numbers(
        self, client: httpx.AsyncClient
    ) -> None:
        """Consent restrictions are visible; the phone numbers are not."""
        body = (await client.get("/meta")).json()
        assert body["allowlist_size"] == 2
        assert "9000000001" not in response_text(body)

    async def test_reports_the_calling_window(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/meta")).json()
        assert "10:00" in body["calling_window"]
        assert "Asia/Kolkata" in body["calling_window"]


def response_text(body: object) -> str:
    import json

    return json.dumps(body)


class TestRequestCorrelation:
    async def test_echoes_a_supplied_request_id(self, client: httpx.AsyncClient) -> None:
        """Lets a caller tie its own logs to ours across a failure."""
        response = await client.get("/healthz", headers={"X-Request-ID": "abc123"})
        assert response.headers["X-Request-ID"] == "abc123"

    async def test_generates_a_request_id_when_absent(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.headers.get("X-Request-ID")


class TestErrorEnvelope:
    async def test_unknown_routes_use_the_standard_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        """One error shape everywhere, so the frontend needs one handler."""
        response = await client.get("/no-such-route")
        assert response.status_code == 404

        body = response.json()
        assert set(body) == {"error", "request_id"}
        assert set(body["error"]) == {"code", "message", "details"}
        assert body["error"]["code"] == "http_404"
