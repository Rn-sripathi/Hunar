"""Tests for the request-model constraints.

These matter for a practical reason beyond correctness: the assignment's
API key is time-limited and call minutes are finite, so a request we could
have known was invalid must never cost a round trip. Several of Hunar's
rules return an opaque 400, which is exactly why they are enforced locally
with a readable message instead.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from hunar_sdk.enums import CallStatus, Language, VoicePersona
from hunar_sdk.models import (
    AgentCreate,
    BulkCallCreate,
    BulkCallRecipient,
    Call,
    CallbackConfig,
    CallCreate,
    Guardrails,
    RetryConfig,
    normalize_e164,
)

AGENT_ID = uuid4()


def minimal_agent(**overrides: object) -> AgentCreate:
    payload: dict[str, object] = {
        "name": "screening-agent",
        "voice_persona": VoicePersona.NEHA,
        "agent_prompt": "Ask the candidate two questions.",
        "objective": "Screen the candidate for the role.",
        "introduction": "Hi, is this a good time to talk?",
        "result_prompt": "Extract the fields from the transcript.",
        "result_schema": {"interested": "boolean"},
    }
    payload.update(overrides)
    return AgentCreate.model_validate(payload)


class TestNormalizeE164:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("+919876543210", "+919876543210"),
            ("+91 98765 43210", "+919876543210"),
            ("+91-98765-43210", "+919876543210"),
            ("  +919876543210  ", "+919876543210"),
            ("+1 (415) 555-0123", "+14155550123"),
        ],
    )
    def test_normalises_operator_formatting(self, raw: str, expected: str) -> None:
        assert normalize_e164(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "9876543210",  # no country code
            "+0987654321",  # country codes never start with zero
            "+91",  # too short
            "+9198765432109876",  # too long
            "not a number",
            "",
        ],
    )
    def test_rejects_invalid_numbers(self, raw: str) -> None:
        with pytest.raises(ValueError, match=r"E\.164"):
            normalize_e164(raw)

    def test_passes_none_through(self) -> None:
        assert normalize_e164(None) is None


class TestAgentCreate:
    def test_accepts_a_valid_payload(self) -> None:
        agent = minimal_agent()
        assert agent.language is Language.ENGLISH  # documented default

    def test_requires_a_non_empty_result_schema(self) -> None:
        """An agent with no schema would extract nothing, which is useless."""
        with pytest.raises(ValidationError):
            minimal_agent(result_schema={})

    @pytest.mark.parametrize("key", ["Interested", "has-dash", "2fast", "with space", ""])
    def test_rejects_schema_keys_that_are_not_snake_case(self, key: str) -> None:
        """Schema keys become table columns and result-payload keys.

        Rejecting them early keeps the dashboard's column derivation and
        the coercion layer from having to defend against odd identifiers.
        """
        with pytest.raises(ValidationError):
            minimal_agent(result_schema={key: "string"})

    @pytest.mark.parametrize("name", ["ab", "x" * 65])
    def test_enforces_the_name_length_bounds(self, name: str) -> None:
        with pytest.raises(ValidationError):
            minimal_agent(name=name)

    def test_rejects_unknown_fields(self) -> None:
        """`conclusion` and `silence_response` do not exist on create.

        The published prose mentions them but the schema does not accept
        them, so sending one would be a silent 422 in production.
        """
        with pytest.raises(ValidationError):
            minimal_agent(conclusion="Thanks for your time.")


class TestCallbackConfig:
    def test_accepts_https(self) -> None:
        config = CallbackConfig(call_status_callback_url="https://example.com/hook")
        assert config.call_status_callback_url is not None

    @pytest.mark.parametrize(
        "url", ["http://example.com/hook", "ftp://example.com", "example.com/hook"]
    )
    def test_rejects_anything_other_than_https(self, url: str) -> None:
        """Hunar refuses non-HTTPS callbacks, and so should we."""
        with pytest.raises(ValidationError, match="HTTPS"):
            CallbackConfig(call_status_callback_url=url)

    def test_all_urls_are_optional(self) -> None:
        assert CallbackConfig().call_result_callback_url is None


class TestRetryConfig:
    @pytest.mark.parametrize("hours", [0, 3, 6, 9, 12, 24])
    def test_accepts_the_permitted_cadences(self, hours: int) -> None:
        assert RetryConfig(max_retry_count=2, retry_interval_hours=hours)

    @pytest.mark.parametrize("hours", [1, 2, 4, 5, 8, 13, 23, 48])
    def test_rejects_any_other_cadence(self, hours: int) -> None:
        with pytest.raises(ValidationError, match="retry_interval_hours"):
            RetryConfig(max_retry_count=2, retry_interval_hours=hours)

    @pytest.mark.parametrize("count", [-1, 11, 100])
    def test_bounds_the_retry_count(self, count: int) -> None:
        with pytest.raises(ValidationError):
            RetryConfig(max_retry_count=count, retry_interval_hours=6)

    def test_both_fields_are_required_together(self) -> None:
        """Hunar rejects a partial retry_config with a bare 400."""
        with pytest.raises(ValidationError):
            RetryConfig.model_validate({"max_retry_count": 3})


class TestGuardrails:
    def test_accepts_a_valid_window(self) -> None:
        rails = Guardrails(
            allowed_days=["mon", "TUE", "wed"],
            earliest_call_time="10:00",
            last_call_time="19:00",
        )
        assert rails.allowed_days == ["MON", "TUE", "WED"]  # normalised upward

    def test_requires_at_least_three_distinct_days(self) -> None:
        with pytest.raises(ValidationError):
            Guardrails(
                allowed_days=["MON", "MON", "MON"],
                earliest_call_time="10:00",
                last_call_time="19:00",
            )

    def test_rejects_unknown_day_codes(self) -> None:
        with pytest.raises(ValidationError, match="unknown day codes"):
            Guardrails(
                allowed_days=["MON", "TUE", "FUNDAY"],
                earliest_call_time="10:00",
                last_call_time="19:00",
            )

    def test_requires_a_three_hour_window(self) -> None:
        with pytest.raises(ValidationError, match="at least 3 hours"):
            Guardrails(
                allowed_days=["MON", "TUE", "WED"],
                earliest_call_time="10:00",
                last_call_time="12:00",
            )

    @pytest.mark.parametrize("value", ["25:00", "9:00", "10:60", "ten", "10-00"])
    def test_rejects_malformed_times(self, value: str) -> None:
        with pytest.raises(ValidationError):
            Guardrails(
                allowed_days=["MON", "TUE", "WED"],
                earliest_call_time=value,
                last_call_time="19:00",
            )


class TestCallCreate:
    def test_defaults_to_indian_time(self) -> None:
        call = CallCreate(callee_name="Asha", mobile_number="+919876543210", agent_id=AGENT_ID)
        assert call.timezone == "Asia/Kolkata"

    def test_normalises_the_mobile_number(self) -> None:
        call = CallCreate(callee_name="Asha", mobile_number="+91 98765 43210", agent_id=AGENT_ID)
        assert call.mobile_number == "+919876543210"

    def test_rejects_a_number_without_a_country_code(self) -> None:
        with pytest.raises(ValidationError):
            CallCreate(callee_name="Asha", mobile_number="9876543210", agent_id=AGENT_ID)

    def test_bounds_the_request_id(self) -> None:
        """`request_id` is our idempotency handle and is capped at 64 chars."""
        with pytest.raises(ValidationError):
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=AGENT_ID,
                request_id="x" * 65,
            )


class TestBulkCallCreate:
    def test_defaults_protect_against_bad_rows(self) -> None:
        """Hunar drops invalid and duplicate rows unless told otherwise."""
        bulk = BulkCallCreate(
            agent_id=AGENT_ID,
            data=[BulkCallRecipient(callee_name="Asha", mobile_number="+919876543210")],
        )
        assert bulk.remove_invalid_rows is True
        assert bulk.remove_duplicate_phone_numbers is True

    def test_requires_at_least_one_recipient(self) -> None:
        with pytest.raises(ValidationError):
            BulkCallCreate(agent_id=AGENT_ID, data=[])

    def test_caps_at_ten_thousand_recipients(self) -> None:
        recipient = BulkCallRecipient(callee_name="A", mobile_number="+919876543210")
        with pytest.raises(ValidationError):
            BulkCallCreate(agent_id=AGENT_ID, data=[recipient] * 10_001)


class TestCallResponse:
    def test_tolerates_unknown_status_values(self) -> None:
        """A new status must not break deserialisation of a whole page."""
        call = Call.model_validate({"id": str(uuid4()), "status": "TELEPORTING"})
        assert call.status is CallStatus.UNKNOWN

    def test_preserves_unknown_extra_fields(self) -> None:
        call = Call.model_validate(
            {"id": str(uuid4()), "status": "COMPLETED", "brand_new_field": 42}
        )
        assert call.status is CallStatus.COMPLETED

    @pytest.mark.parametrize(
        ("status", "terminal"),
        [
            ("COMPLETED", True),
            ("NOT_CONNECTED", True),
            ("FAILED", True),
            ("CANCELLED", True),
            ("RINGING", False),
            ("IN_PROGRESS", False),
            ("NOT_STARTED", False),
            ("SCHEDULED", False),
        ],
    )
    def test_reports_terminality_correctly(self, status: str, terminal: bool) -> None:
        """Polling stops on terminality, so this drives real behaviour."""
        call = Call.model_validate({"id": str(uuid4()), "status": status})
        assert call.is_terminal is terminal

    def test_accepts_stringified_result_values(self) -> None:
        """A field declared "boolean" comes back as "Yes".

        This is the exact reason a coercion layer exists downstream.
        """
        call = Call.model_validate(
            {
                "id": str(uuid4()),
                "status": "COMPLETED",
                "result": {"interested": "Yes", "years_experience": "3"},
            }
        )
        assert call.result == {"interested": "Yes", "years_experience": "3"}
