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
    public_api_base_url: str = "https://localhost"

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
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"

    # ── access ───────────────────────────────────────────────
    demo_password: str = ""
    session_secret: str = INSECURE_SESSION_SECRET
    cors_origins: str = "http://localhost:3000"

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
            if not self.public_api_base_url.startswith("https://"):
                raise ValueError(
                    "PUBLIC_API_BASE_URL must be HTTPS in production; Hunar "
                    "rejects non-HTTPS callback URLs"
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
