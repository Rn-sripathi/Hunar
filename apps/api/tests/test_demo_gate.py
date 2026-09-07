"""The shared-password gate on a public deployment.

Worth stating why this file exists. The gate was described in the plan,
described in the README, and never written. The deployment went live
serving a real candidate's name and unmasked mobile number to anyone who
had the URL, and nothing failed, because nothing was checking.

So the tests here assert the negative case first: that a request without
the cookie is refused. A gate is the kind of thing that appears to work
in every test that only exercises the happy path.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import HunarMode, PeopleProvider, Settings
from app.core.gate import (
    COOKIE_NAME,
    MAX_AGE_SECONDS,
    issue_token,
    token_is_valid,
)
from app.main import create_app

PASSWORD = "a-shared-demo-password"
SECRET = "a-session-secret-long-enough-to-be-real"
ORIGIN = "https://frontend.example"


@pytest.fixture
def gated_settings() -> Settings:
    """Settings with a password configured, as a deployment has."""
    return Settings(
        environment="local",
        log_level="WARNING",
        hunar_mode=HunarMode.MOCK,
        hunar_api_key="test-key-not-real",
        people_provider=PeopleProvider.FIXTURE,
        database_url="sqlite+aiosqlite:///:memory:",
        public_api_base_url="https://test.invalid",
        openai_api_key="",
        demo_password=PASSWORD,
        session_secret=SECRET,
    )


@pytest.fixture
async def gated_client(gated_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client against a gated app, with no database behind it.

    The gate must refuse before anything touches the database, so these
    tests deliberately do not create a schema. A 401 proves the request
    stopped at the middleware; a 500 would prove it did not.
    """
    application: FastAPI = create_app(gated_settings)
    # Errors past the gate become responses rather than propagating.
    # These tests deliberately have no database, so getting through
    # the gate *should* fail — and 'anything but 401' is the point.
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client


class TestTokens:
    def test_a_freshly_minted_token_is_valid(self) -> None:
        assert token_is_valid(issue_token(SECRET), SECRET)

    def test_a_token_from_another_secret_is_not(self) -> None:
        """Otherwise anyone could mint their own cookie."""
        assert not token_is_valid(issue_token("a-different-secret"), SECRET)

    def test_a_tampered_token_is_not(self) -> None:
        token = issue_token(SECRET)
        payload, _, signature = token.rpartition(".")
        forged = f"{payload}.{'0' * len(signature)}"
        assert not token_is_valid(forged, SECRET)

    def test_an_expired_token_is_not(self) -> None:
        """A leaked cookie stops working on its own."""
        old = issue_token(SECRET, now=time.time() - MAX_AGE_SECONDS - 10)
        assert not token_is_valid(old, SECRET)

    def test_a_token_from_the_future_is_not(self) -> None:
        """Guards against a clock-skew trick extending the lifetime."""
        assert not token_is_valid(issue_token(SECRET, now=time.time() + 600), SECRET)

    @pytest.mark.parametrize("junk", ["", "nonsense", "v1.abc.def", "v2.1.2", "a.b"])
    def test_malformed_tokens_are_rejected_not_crashed(self, junk: str) -> None:
        assert not token_is_valid(junk, SECRET)


class TestGate:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/hiring/jobs",
            "/api/v1/people/policy",
            "/api/v1/people/prospects",
            "/api/v1/campaigns",
        ],
    )
    async def test_data_is_refused_without_the_password(
        self, gated_client: httpx.AsyncClient, path: str
    ) -> None:
        """The exact failure that shipped.

        `/api/v1/hiring/jobs/{id}/candidates` was returning a real
        person's unmasked mobile number to unauthenticated callers.
        """
        response = await gated_client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "locked"

    @pytest.mark.parametrize("path", ["/healthz", "/docs", "/openapi.json"])
    async def test_health_and_docs_stay_open(
        self, gated_client: httpx.AsyncClient, path: str
    ) -> None:
        """Gating the health check reports the service as dead forever.

        The docs are open on purpose: reading the schema is a feature for
        a reviewer, and it carries no data and no credentials.
        """
        response = await gated_client.get(path)
        assert response.status_code == 200, path

    async def test_meta_is_gated(self, gated_client: httpx.AsyncClient) -> None:
        """It is small, but it is still configuration.

        /meta reports the voice mode, the people provider and how many
        numbers are consented. None of that needs to be public, and the
        UI only reads it after unlocking anyway.
        """
        assert (await gated_client.get("/meta")).status_code == 401

    async def test_webhooks_are_not_gated(self, gated_client: httpx.AsyncClient) -> None:
        """Hunar cannot log in.

        That path is authenticated by an HMAC over the raw body instead,
        which is stronger than a shared password. What matters here is
        only that the gate does not answer first.
        """
        response = await gated_client.post(
            "/webhooks/hunar/some-token/status", json={"event_type": "x"}
        )
        assert response.status_code != 401

    async def test_the_wrong_password_is_refused(self, gated_client: httpx.AsyncClient) -> None:
        response = await gated_client.post("/api/unlock", json={"password": "not-the-password"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "bad_password"
        assert COOKIE_NAME not in response.cookies

    async def test_a_missing_password_field_is_refused_not_crashed(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        assert (await gated_client.post("/api/unlock", json={})).status_code == 401
        assert (await gated_client.post("/api/unlock", content=b"")).status_code == 401

    async def test_the_right_password_opens_the_gate(self, gated_client: httpx.AsyncClient) -> None:
        unlocked = await gated_client.post("/api/unlock", json={"password": PASSWORD})
        assert unlocked.status_code == 200
        assert COOKIE_NAME in unlocked.cookies

        # Past the gate now. There is no schema behind this app, so
        # anything other than 401 proves the request got through — which
        # is precisely what is being asserted.
        after = await gated_client.get("/api/v1/people/policy")
        assert after.status_code != 401

    async def test_the_cookie_is_not_readable_by_scripts(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        """HttpOnly, so a cross-site script cannot exfiltrate it."""
        response = await gated_client.post("/api/unlock", json={"password": PASSWORD})
        header = response.headers["set-cookie"].lower()
        assert "httponly" in header
        assert "secure" in header
        # `none` because the frontend and the API are on different sites;
        # a lax cookie would never be sent at all.
        assert "samesite=none" in header

    async def test_a_forged_cookie_does_not_open_the_gate(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        gated_client.cookies.set(COOKIE_NAME, issue_token("some-other-secret"), domain="testserver")
        response = await gated_client.get("/api/v1/people/policy")
        assert response.status_code == 401


class TestRejectionsStayLegibleToBrowsers:
    """A 401 the browser cannot read is worse than no gate at all.

    The gate shipped installed *after* the CORS middleware, which in
    Starlette means it ran outside it. Its 401 therefore carried no
    Access-Control-Allow-Origin header: the preflight passed, the real
    response was discarded by the browser, and the frontend could not
    read the one status code that tells it to ask for a password. The
    deployed app reported "cannot reach the API" while the API was
    answering correctly.

    Anything able to reject a request must sit inside CORS.
    """

    @pytest.fixture
    def gated_settings(self) -> Settings:
        return Settings(
            environment="local",
            log_level="WARNING",
            hunar_mode=HunarMode.MOCK,
            hunar_api_key="test-key-not-real",
            people_provider=PeopleProvider.FIXTURE,
            database_url="sqlite+aiosqlite:///:memory:",
            public_api_base_url="https://test.invalid",
            openai_api_key="",
            demo_password=PASSWORD,
            session_secret=SECRET,
            cors_origins=ORIGIN,
        )

    async def test_the_locked_401_carries_cors_headers(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        response = await gated_client.get("/api/v1/hiring/jobs", headers={"Origin": ORIGIN})
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == ORIGIN, (
            "without this header the browser discards the body, so the "
            "frontend never learns it needs a password"
        )
        assert response.headers.get("access-control-allow-credentials") == "true"

    async def test_a_rejected_password_also_carries_them(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        """Otherwise "wrong password" is indistinguishable from "offline"."""
        response = await gated_client.post(
            "/api/unlock",
            json={"password": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == ORIGIN

    async def test_a_successful_unlock_carries_them(self, gated_client: httpx.AsyncClient) -> None:
        response = await gated_client.post(
            "/api/unlock",
            json={"password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == ORIGIN


class TestUngated:
    async def test_no_password_means_no_gate(self, client: httpx.AsyncClient) -> None:
        """Local development is untouched.

        The default settings configure no password, so the middleware is
        never installed and nobody is prompted while working offline.
        """
        assert (await client.get("/api/v1/hiring/jobs")).status_code == 200
