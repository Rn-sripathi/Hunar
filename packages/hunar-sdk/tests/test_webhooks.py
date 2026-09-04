"""Tests for inbound webhook signature verification.

This is the security boundary of the whole application: anything that
passes here is trusted enough to mutate call records. The cases below
therefore cover not just the happy path but every way a forgery or a
replay could sneak through, plus the two operational realities the
documentation reveals — Hunar may send several comma-separated signatures
at once, and it retries deliveries for up to eight minutes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from hunar_sdk.webhooks import (
    DEFAULT_MAX_SKEW_SECONDS,
    compute_signature,
    verify_webhook,
)

KEY = "hunar_test_key_primary"
OLD_KEY = "hunar_test_key_rotated_out"
OTHER_KEY = "hunar_test_key_attacker"
BODY = b'{"event_type":"call_result_done","result":{"interested":"Yes"}}'
NOW = 1_757_000_000.0
TS = str(int(NOW))


def sign(key: str = KEY, timestamp: str = TS, body: bytes = BODY) -> str:
    return compute_signature(key, timestamp, body)


class TestComputeSignature:
    def test_matches_documented_construction(self) -> None:
        """The signed message is f"{timestamp}." followed by the raw body."""
        expected_message = f"{TS}.".encode() + BODY
        expected = base64.b64encode(
            hmac.new(KEY.encode(), expected_message, hashlib.sha256).digest()
        ).decode()
        assert compute_signature(KEY, TS, BODY) == expected

    def test_is_deterministic(self) -> None:
        assert compute_signature(KEY, TS, BODY) == compute_signature(KEY, TS, BODY)

    def test_timestamp_is_covered(self) -> None:
        """Changing only the timestamp must change the signature.

        Otherwise a captured signature would stay valid forever simply by
        advancing the timestamp header.
        """
        assert compute_signature(KEY, TS, BODY) != compute_signature(KEY, "1757000001", BODY)

    def test_body_is_covered_byte_exactly(self) -> None:
        """A single added space invalidates the signature.

        This is why the route must read raw bytes before parsing, and why
        no body-rewriting middleware may sit on the webhook path.
        """
        assert compute_signature(KEY, TS, BODY) != compute_signature(
            KEY, TS, BODY.replace(b"{", b"{ ", 1)
        )

    def test_key_is_the_secret(self) -> None:
        assert compute_signature(KEY, TS, BODY) != compute_signature(OTHER_KEY, TS, BODY)

    def test_surrounding_whitespace_in_timestamp_is_stripped(self) -> None:
        """A header value arriving padded still verifies."""
        assert compute_signature(KEY, f"  {TS}  ", BODY) == compute_signature(KEY, TS, BODY)


class TestVerifyWebhook:
    def test_accepts_a_genuine_request(self) -> None:
        result = verify_webhook(
            signature_header=sign(),
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok
        assert result.reason is None
        assert result.timestamp == int(TS)

    def test_rejects_a_forged_signature(self) -> None:
        result = verify_webhook(
            signature_header=sign(OTHER_KEY),
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "signature_mismatch"

    def test_rejects_a_tampered_body(self) -> None:
        """A valid signature over different content must not validate."""
        tampered = BODY.replace(b'"Yes"', b'"No"')
        result = verify_webhook(
            signature_header=sign(),
            timestamp_header=TS,
            body=tampered,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "signature_mismatch"

    @pytest.mark.parametrize(
        ("signature", "timestamp"),
        [(None, TS), (sign(), None), (None, None), ("", TS), (sign(), "")],
    )
    def test_rejects_missing_headers(self, signature: str | None, timestamp: str | None) -> None:
        result = verify_webhook(
            signature_header=signature,
            timestamp_header=timestamp,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "missing_headers"

    @pytest.mark.parametrize("timestamp", ["not-a-number", "12.5", "1757000000abc", "  "])
    def test_rejects_malformed_timestamp(self, timestamp: str) -> None:
        result = verify_webhook(
            signature_header=sign(timestamp=timestamp),
            timestamp_header=timestamp,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason in {"malformed_timestamp", "missing_headers"}

    def test_rejects_a_stale_replay(self) -> None:
        """An old but correctly signed request is refused."""
        old = str(int(NOW - DEFAULT_MAX_SKEW_SECONDS - 30))
        result = verify_webhook(
            signature_header=sign(timestamp=old),
            timestamp_header=old,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "timestamp_out_of_window"

    def test_rejects_a_far_future_timestamp(self) -> None:
        """Clock skew is bounded in both directions."""
        future = str(int(NOW + DEFAULT_MAX_SKEW_SECONDS + 30))
        result = verify_webhook(
            signature_header=sign(timestamp=future),
            timestamp_header=future,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "timestamp_out_of_window"

    def test_accepts_at_the_window_edge(self) -> None:
        edge = str(int(NOW - DEFAULT_MAX_SKEW_SECONDS))
        result = verify_webhook(
            signature_header=sign(timestamp=edge),
            timestamp_header=edge,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok

    def test_widened_window_admits_an_eight_minute_retry(self) -> None:
        """Hunar retries at 1, 2, 4 and 8 minutes.

        Eight minutes is 480 seconds, beyond the conventional 300-second
        tolerance. If Hunar reuses the original timestamp on retries, the
        default window silently drops the final attempt. This test pins the
        behaviour so widening the window remains a configuration change.
        """
        eight_minutes_ago = str(int(NOW - 480))
        signature = sign(timestamp=eight_minutes_ago)

        default_window = verify_webhook(
            signature_header=signature,
            timestamp_header=eight_minutes_ago,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not default_window.ok
        assert default_window.reason == "timestamp_out_of_window"

        widened = verify_webhook(
            signature_header=signature,
            timestamp_header=eight_minutes_ago,
            body=BODY,
            trusted_keys=[KEY],
            max_skew_seconds=900,
            now=NOW,
        )
        assert widened.ok

    def test_accepts_one_of_several_comma_separated_signatures(self) -> None:
        """Hunar signs with every active key and sends them comma-joined."""
        header = f"{sign(OTHER_KEY)},{sign(KEY)},{sign(OLD_KEY)}"
        result = verify_webhook(
            signature_header=header,
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok

    def test_tolerates_whitespace_around_comma_separated_values(self) -> None:
        header = f" {sign(OTHER_KEY)} ,  {sign(KEY)} "
        result = verify_webhook(
            signature_header=header,
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok

    def test_verifies_against_a_rotated_key(self) -> None:
        """A webhook signed with the previous key still verifies.

        Without this, rotating the API key would silently drop in-flight
        deliveries for as long as Hunar's retry window lasts.
        """
        result = verify_webhook(
            signature_header=sign(OLD_KEY),
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY, OLD_KEY],
            now=NOW,
        )
        assert result.ok

    def test_rejects_when_no_signature_survives_parsing(self) -> None:
        result = verify_webhook(
            signature_header=" , , ",
            timestamp_header=TS,
            body=BODY,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "empty_signature"

    @pytest.mark.parametrize("keys", [[], [""], ["", ""]])
    def test_rejects_when_no_usable_key_is_configured(self, keys: list[str]) -> None:
        """A misconfigured backend must fail closed, never open."""
        result = verify_webhook(
            signature_header=sign(),
            timestamp_header=TS,
            body=BODY,
            trusted_keys=keys,
            now=NOW,
        )
        assert not result.ok
        assert result.reason == "no_trusted_keys"

    def test_verifies_an_empty_body(self) -> None:
        """Some events may post no body; the timestamp still authenticates."""
        result = verify_webhook(
            signature_header=compute_signature(KEY, TS, b""),
            timestamp_header=TS,
            body=b"",
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok

    def test_verifies_a_utf8_body_byte_exactly(self) -> None:
        body = '{"name":"अनुराग","result":"हाँ"}'.encode()
        result = verify_webhook(
            signature_header=compute_signature(KEY, TS, body),
            timestamp_header=TS,
            body=body,
            trusted_keys=[KEY],
            now=NOW,
        )
        assert result.ok
