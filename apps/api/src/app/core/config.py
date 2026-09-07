"""Application configuration.

Every setting is typed and validated at import time, so a missing or
malformed value fails the process on startup rather than surfacing as a
confusing runtime error hours later. On a three-day deadline that
distinction is worth real money: a webhook URL that is silently HTTP
rather than HTTPS would otherwise present as calls that mysteriously
never report their results.

Two settings deserve particular attention because they encode decisions
rather than mere values:

* ``hunar_mode`` chooses between the live API and the demo client. The
  default is ``auto``, which starts live and latches to demo the first
  time the key is rejected or the quota is exhausted, so the deployed
  link keeps working after the assignment key expires.
* ``demo_allowlist`` is the outbound calling gate for the sourcing app.
  It lives in the environment rather than the repository because it
  contains real phone numbers, and because a deployment should not be
  able to dial anything the operator has not explicitly consented to.
"""

from __future__ import annotations

import os
import re
from datetime import time
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "AllowlistEntry",
    "HunarMode",
    "PeopleProvider",
    "Settings",
    "get_settings",
]

#: Distinguishes "nobody said" from "somebody chose localhost", which is
#: what lets the platform's own URL be adopted without overriding a
#: deliberate local choice.
_UNSET_PUBLIC_URL = "https://localhost"

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")

#: The repository root, resolved from this file rather than from the
#: working directory. Alembic runs from ``apps/api`` and uvicorn from the
#: repository root, so a relative ``.env`` would be found by one and not
#: the other, and the failure would look like a missing API key rather
#: than a missing file.
_REPO_ROOT = Path(__file__).resolve().parents[5]

#: Sentinel value for the session secret. Not a credential; it exists so a
#: production deploy that forgot to set a real one is refused at startup
#: rather than signing cookies with a value published in this repository.
INSECURE_SESSION_SECRET = "insecure-local-only-secret"  # noqa: S105


class HunarMode(StrEnum):
    """How the application talks to the Hunar voice API."""

    LIVE = "live"
    MOCK = "mock"
    #: Start live, fall back to mock on the first 401 or 402, and say so in
    #: the UI. This is what production deploys with.
    AUTO = "auto"


class PeopleProvider(StrEnum):
    """Source of prospect data for the sourcing app.

    Deliberately independent of :class:`HunarMode`: the voice key and the
    people-data credits expire on different schedules, and one failing
    must not force the other into demo mode.
    """

    PDL = "pdl"
    #: People Data Labs' sandbox. Free, consumes no credits, returns
    #: artificial records. The safety net when monthly credits run out.
    PDL_SANDBOX = "pdl_sandbox"
    FIXTURE = "fixture"


class AllowlistEntry(BaseModel):
    """One number that outbound calls are permitted to reach."""

    e164: str
    label: str

    @field_validator("e164")
    @classmethod
    def _validate(cls, value: str) -> str:
        candidate = re.sub(r"[\s\-()]", "", value.strip())
        if not _E164.match(candidate):
            raise ValueError(f"allowlist entry {value!r} is not a valid E.164 number")
        return candidate


class Settings(BaseSettings):
    """Validated application settings, loaded from the environment."""

    model_config = SettingsConfigDict(
        # Both are consulted, later winning. The repository root covers
        # every tool regardless of where it was invoked; the bare name
        # still allows a per-directory override in local experiments.
        env_file=(_REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── environment ──────────────────────────────────────────
    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_prefix: str = "/api/v1"

    # ── Hunar voice API ──────────────────────────────────────
    hunar_api_key: str = ""
    hunar_base_url: str = "https://api.voice.hunar.ai/external/v1/"
    hunar_mode: HunarMode = HunarMode.AUTO
    hunar_agent_strategy: Literal["per_job", "shared"] = "per_job"

    #: Comma-separated keys rotated out recently. Webhooks signed with one
    #: of these still verify, so rotation does not drop in-flight events.
    hunar_api_keys_previous: str = ""

    #: Replay tolerance for inbound webhooks. Hunar retries at 1, 2, 4 and
    #: 8 minutes; an 8-minute retry is 480s old, so if Hunar reuses the
    #: original timestamp the default 300 would silently drop the last
    #: attempt. Configurable precisely because that is unverified.
    webhook_max_skew_seconds: int = Field(default=300, ge=60, le=3600)

    #: Root of every webhook callback URL. Must be public and HTTPS.
    #:
    #: Left at the sentinel, it is filled in from the platform's own
    #: announcement of where it is hosted, so a first deploy does not have
    #: to know its URL before it exists.
    public_api_base_url: str = _UNSET_PUBLIC_URL

    #: Speed multiplier for the demo client's simulated call lifecycle.
    #: Left at 1.0 for the deployed demo so the monitoring screen animates
    #: believably; raised sharply in tests so a suite is not gated on
    #: thirteen seconds of pretend ringing.
    hunar_fake_speed: float = Field(default=1.0, gt=0)

    #: How stale a non-terminal call may be before a read refreshes it
    #: from the upstream API. This is the only source of live progress,
    #: since Hunar pushes a status webhook only once a call has finished.
    reconcile_interval_seconds: float = Field(default=8.0, ge=0)

    # ── database ─────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hunar"
    db_echo: bool = False
    db_pool_size: Annotated[int, Field(ge=1, le=20)] = 5

    # ── people search ────────────────────────────────────────
    people_provider: PeopleProvider = PeopleProvider.FIXTURE
    pdl_api_key: str = ""
    people_max_records_per_search: Annotated[int, Field(ge=1, le=100)] = 25
    people_daily_credit_cap: Annotated[int, Field(ge=1, le=1000)] = 40

    # ── consent gate ─────────────────────────────────────────
    #: ``+91XXXXXXXXXX|Label`` pairs, comma-separated.
    demo_allowlist: str = ""
    calling_hours_start: str = "10:00"
    calling_hours_end: str = "19:00"
    calling_timezone: str = "Asia/Kolkata"

    # ── LLM ──────────────────────────────────────────────────
    openai_api_key: str = ""

    #: Must support structured outputs, since the extractor asks for a
    #: schema-validated object rather than parsing prose. Configurable
    #: because which models an account can reach varies.
    llm_model: str = "gpt-4o"

    # ── access ───────────────────────────────────────────────
    demo_password: str = ""
    session_secret: str = INSECURE_SESSION_SECRET

    #: Both spellings of the local frontend are allowed by default.
    #: `localhost` and `127.0.0.1` are different origins to a browser, so
    #: allowing only one produces a blank dashboard whose requests fail
    #: silently in the console while the API looks perfectly healthy.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── validation ───────────────────────────────────────────
    @field_validator("hunar_base_url", "public_api_base_url")
    @classmethod
    def _strip_trailing_space(cls, value: str) -> str:
        return value.strip()

    @field_validator("calling_hours_start", "calling_hours_end")
    @classmethod
    def _validate_clock_time(cls, value: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", value.strip()):
            raise ValueError(f"expected HH:MM in 24-hour form, got {value!r}")
        return value.strip()

    @model_validator(mode="after")
    def _adopt_host_provided_url(self) -> Settings:
        """Take the public URL from the platform when it offers one.

        Every webhook callback is rooted at this value, and Hunar rejects
        anything that is not HTTPS, so production refuses to boot without
        it. That created a chicken and egg: the correct value is the
        service's own URL, which does not exist until the service has been
        created, so a first deploy could only ever fail.

        Render publishes ``RENDER_EXTERNAL_URL``, so the common case needs
        no human in the loop. An explicit ``PUBLIC_API_BASE_URL`` still
        wins, which matters for a tunnel during local development against
        the live API.
        """
        if self.public_api_base_url in ("", _UNSET_PUBLIC_URL):
            for variable in ("PUBLIC_API_BASE_URL", "RENDER_EXTERNAL_URL"):
                provided = os.environ.get(variable, "").strip().rstrip("/")
                if provided:
                    self.public_api_base_url = provided
                    break
        return self

    @model_validator(mode="after")
    def _validate_consistency(self) -> Settings:
        if self.calling_window_start >= self.calling_window_end:
            raise ValueError("calling_hours_start must be earlier than calling_hours_end")

        if self.hunar_mode in (HunarMode.LIVE, HunarMode.AUTO) and not self.hunar_api_key:
            raise ValueError(
                f"hunar_mode={self.hunar_mode.value} requires HUNAR_API_KEY. "
                "Set HUNAR_MODE=mock to run without a key."
            )

        if self.is_production:
            # These are the three ways a deploy can be quietly unsafe, so
            # they are refused outright rather than warned about.
            # The sentinel is checked as well as the scheme. It is
            # "https://localhost", which satisfies an HTTPS test while
            # being unreachable from the internet, so testing the scheme
            # alone let a production deploy boot with callbacks that could
            # never be delivered — and the only symptom would be results
            # arriving late, via polling, for a reason nobody could see.
            if (
                self.public_api_base_url == _UNSET_PUBLIC_URL
                or not self.public_api_base_url.startswith("https://")
                or not self.webhooks_deliverable
            ):
                raise ValueError(
                    "PUBLIC_API_BASE_URL must be a public HTTPS URL in "
                    "production, because Hunar rejects non-HTTPS callbacks and "
                    "cannot reach a private address. On Render it is taken from "
                    "RENDER_EXTERNAL_URL automatically; set it explicitly on any "
                    f"other host. Got: {self.public_api_base_url!r}"
                )
            if not self.demo_password:
                raise ValueError("DEMO_PASSWORD is required in production")
            if self.session_secret == INSECURE_SESSION_SECRET:
                raise ValueError("SESSION_SECRET must be set in production")
        return self

    # ── derived values ───────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def trusted_webhook_keys(self) -> list[str]:
        """Keys accepted when verifying an inbound webhook signature.

        The current key first, then any recently rotated ones. Hunar signs
        with every active key and sends the signatures comma-separated, so
        supplying several here is what makes rotation seamless.
        """
        keys = [self.hunar_api_key.strip()]
        keys.extend(
            part.strip() for part in self.hunar_api_keys_previous.split(",") if part.strip()
        )
        return [key for key in keys if key]

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def calling_window_start(self) -> time:
        return time.fromisoformat(self.calling_hours_start)

    @property
    def calling_window_end(self) -> time:
        return time.fromisoformat(self.calling_hours_end)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowlist(self) -> list[AllowlistEntry]:
        """Parsed outbound calling allowlist.

        A malformed entry raises rather than being skipped. Silently
        dropping one would mean a number the operator believed was
        permitted quietly is not, which is the wrong direction for a
        safety gate to fail in.
        """
        entries: list[AllowlistEntry] = []
        for chunk in self.demo_allowlist.split(","):
            raw = chunk.strip()
            if not raw:
                continue
            number, _, label = raw.partition("|")
            entries.append(AllowlistEntry(e164=number, label=label.strip() or "unlabelled"))
        return entries

    @property
    def allowlisted_numbers(self) -> frozenset[str]:
        return frozenset(entry.e164 for entry in self.allowlist)

    @property
    def webhooks_deliverable(self) -> bool:
        """Whether Hunar could actually reach our webhook endpoint.

        A callback URL pointing at localhost is not merely useless, it is
        worse than sending none: the provider accepts it, tries to
        deliver, retries for eight minutes, and the operator sees nothing
        to explain the silence. Detecting it lets the call be placed
        without callbacks, leaving reconciliation to bring the results
        back, which it does anyway.
        """
        url = self.public_api_base_url.lower()
        if not url.startswith("https://"):
            return False
        host = url.removeprefix("https://").split("/")[0].split(":")[0]
        # These are hosts we refuse to hand out, not an address to bind to,
        # so the "binds to all interfaces" warning does not apply.
        unreachable = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}  # noqa: S104
        return host not in unreachable and not host.endswith(
            (".local", ".internal", ".localdomain")
        )

    def webhook_url(self, token: str, event: str) -> str:
        """Build the callback URL for one call and one event type.

        The correlation token lives in the path because the live
        ``call_result_done`` payload carries no call identifier at all.
        Since ``callback_config`` is supplied per call, embedding it costs
        nothing and makes correlation independent of payload shape.
        """
        return f"{self.public_api_base_url.rstrip('/')}/webhooks/hunar/{token}/{event}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton settings instance.

    Cached so the environment is parsed once and so FastAPI's dependency
    system hands every request the same object.
    """
    return Settings()
