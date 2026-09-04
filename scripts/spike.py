#!/usr/bin/env python
"""Day-zero API spike. Run this BEFORE building anything else.

The assignment key is untested and expires within days, so several design
decisions rest on facts only the live API can settle. This script answers
them in order of how badly a wrong answer would hurt, and captures every
raw response as a fixture so the demo can keep working after the key dies.

What it establishes
-------------------
1. Does the key authenticate at all?
2. **Does the organisation have a validated outbound number?** This is the
   blocking one. Without it no call can be placed and there is no
   workaround on our side, so it has to be discovered in the first minutes
   rather than on the last day.
3. Which ``language`` spelling the API accepts.
4. Whether ``result_schema`` values are read as type names or as free-text
   descriptions.
5. Whether extracted ``result`` values come back typed or stringified.
6. Whether single-brace ``{variable}`` substitution works as documented.

Usage
-----
Read-only probes plus agent creation, spends no call minutes::

    uv run python scripts/spike.py

Also place ONE real call, which does spend minutes::

    uv run python scripts/spike.py --call +919876543210 --confirm

Only ever call a number you own. A 402 mid-assignment is unrecoverable,
so the real call is opt-in and requires both flags.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "hunar-sdk" / "src"))

from hunar_sdk import (  # noqa: E402
    AgentCreate,
    CallCreate,
    CallStatus,
    HunarAuthError,
    HunarError,
    HunarQuotaError,
    Language,
    LiveHunarClient,
    VoicePersona,
)

OUTPUT_DIR = REPO_ROOT / "scripts" / "spike_output"
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 300


# ──────────────────────────────────────────────────────────────
# Tiny .env reader. Deliberately dependency-free so the spike runs
# before the workspace is fully installed.
# ──────────────────────────────────────────────────────────────
def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"


def save(name: str, payload: Any) -> None:
    """Persist a raw response as a fixture.

    These outlive the key, which is the entire point: a captured payload
    is permanent, a live one is not.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{name}.json"
    target.write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    print(f"    saved -> {target.relative_to(REPO_ROOT)}")


def heading(text: str) -> None:
    print(f"\n{'─' * 68}\n{text}\n{'─' * 68}")


def ok(text: str) -> None:
    print(f"  [PASS] {text}")


def fail(text: str) -> None:
    print(f"  [FAIL] {text}")


def note(text: str) -> None:
    print(f"         {text}")


# ──────────────────────────────────────────────────────────────
# Probes
# ──────────────────────────────────────────────────────────────
async def probe_auth(client: LiveHunarClient, findings: dict[str, Any]) -> bool:
    heading("1. Authentication  ·  GET /agents/")
    try:
        page = await client.list_agents(page_size=50)
    except HunarAuthError:
        fail("Key rejected with 401. It is invalid, revoked or already expired.")
        note("Nothing else can proceed. Request a fresh key before continuing.")
        findings["auth"] = "rejected"
        return False
    except HunarQuotaError:
        fail("402: the subscription is expired or minutes are exhausted.")
        note("The key authenticates but cannot be used. Treat as blocking.")
        findings["auth"] = "quota_exhausted"
        return False
    except HunarError as exc:
        fail(f"Unexpected failure: {exc}")
        findings["auth"] = f"error:{exc}"
        return False

    ok(f"Key authenticates. The organisation has {len(page.results)} existing agent(s).")
    findings["auth"] = "ok"
    findings["existing_agent_count"] = len(page.results)
    save("agents_list", [a.model_dump(mode="json") for a in page.results])

    # An existing agent is the cheapest ground truth for the enum spellings
    # our own models guess at.
    if page.results:
        sample = page.results[0]
        note(
            f"Sample agent language={sample.language.value!r} persona={sample.voice_persona.value!r}"
        )
        findings["observed_language_value"] = sample.language.value
        findings["observed_persona_value"] = sample.voice_persona.value
        if sample.result_schema:
            note(f"Sample result_schema: {json.dumps(sample.result_schema)[:300]}")
            findings["observed_result_schema"] = sample.result_schema
    return True


async def probe_numbers(client: LiveHunarClient, findings: dict[str, Any]) -> bool:
    heading("2. Outbound caller ID  ·  GET /numbers/   [BLOCKING]")
    try:
        page = await client.list_numbers()
    except HunarError as exc:
        fail(f"Could not list numbers: {exc}")
        findings["numbers"] = f"error:{exc}"
        return False

    save("numbers_list", [n.model_dump(mode="json") for n in page.results])
    if not page.results:
        fail("The organisation has NO validated outbound numbers.")
        note("Calls will likely fail with a 400 and there is no fix on our side.")
        note("Ask Hunar to provision a number now. Meanwhile build against mock mode.")
        findings["numbers"] = "none"
        return False

    ok(f"{len(page.results)} validated number(s) available.")
    for number in page.results:
        countries = ", ".join(number.allowed_countries) or "unspecified"
        note(f"{number.phone_number}  countries={countries}  validated={number.is_validated}")
    findings["numbers"] = [n.phone_number for n in page.results]
    return True


async def probe_agent_creation(client: LiveHunarClient, findings: dict[str, Any]) -> UUID | None:
    heading("3. Agent creation  ·  POST /agents/")

    # Two schema entries are worded as type names and one as a free-text
    # description. Whatever comes back on a real call tells us which the
    # platform actually honours.
    payload = AgentCreate(
        name="spike-probe-agent",
        voice_persona=VoicePersona.NEHA,
        language=Language.ENGLISH,
        persona_name="Neha",
        introduction=(
            "Hi, am I speaking with {candidate_name}? This is Neha calling from "
            "{company_name} about a short screening question."
        ),
        objective=(
            "Confirm the candidate's identity, ask how many years of experience they "
            "have, and find out whether they are interested in the role."
        ),
        agent_prompt=(
            "You are Neha, a recruitment screening assistant for {company_name}.\n"
            "Speak plainly and keep every reply under 25 words.\n"
            "Ask exactly two questions, one at a time:\n"
            "1. How many years of relevant work experience do you have?\n"
            "2. Are you interested in hearing more about this role?\n"
            "Then thank them and end the call. Never invent details about pay."
        ),
        result_prompt=(
            "From the transcript, extract the fields below using only what the person "
            "actually said. Return an empty string when something was not discussed. "
            "For years_experience answer with digits only. For interested answer "
            "true or false."
        ),
        result_schema={
            "years_experience": "number",
            "interested": "boolean",
            "call_summary": "a one sentence summary of the conversation",
        },
    )

    try:
        agent = await client.create_agent(payload)
    except HunarError as exc:
        fail(f"Agent creation failed: {exc}")
        note("If this is a 422, our AgentCreate model disagrees with the live schema.")
        findings["agent_creation"] = f"error:{exc}"
        return None

    ok(f"Agent created: {agent.id}")
    save("agent_created", agent.model_dump(mode="json"))
    findings["agent_creation"] = "ok"
    findings["spike_agent_id"] = str(agent.id)

    # Read it back: the round trip shows what the platform stored versus
    # what we sent, including how it interpreted result_schema.
    fetched = await client.get_agent(agent.id)
    save("agent_fetched", fetched.model_dump(mode="json"))

    if fetched.result_schema != payload.result_schema:
        note("result_schema was TRANSFORMED on the server:")
        note(f"  sent:     {json.dumps(payload.result_schema)}")
        note(f"  returned: {json.dumps(fetched.result_schema)}")
        findings["result_schema_transformed"] = True
    else:
        note("result_schema was stored verbatim, so the values are opaque to Hunar.")
        note("Conclusion: put all extraction nuance in result_prompt, not the schema.")
        findings["result_schema_transformed"] = False

    if fetched.custom_variables:
        ok(f"Detected prompt variables: {fetched.custom_variables}")
        note("Confirms single-brace {variable} templating is parsed.")
        findings["custom_variables_detected"] = fetched.custom_variables
    else:
        note("No custom_variables reported. Brace templating is UNCONFIRMED.")
        note("Verify by listening to the test call: does it speak the real name?")
        findings["custom_variables_detected"] = []

    return agent.id


async def probe_real_call(
    client: LiveHunarClient,
    agent_id: UUID,
    mobile: str,
    findings: dict[str, Any],
) -> None:
    heading(f"4. Live call  ·  POST /calls/  ->  {mobile}   [SPENDS MINUTES]")

    request_id = f"spike-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    payload = CallCreate(
        callee_name="Rishwanth",
        mobile_number=mobile,
        agent_id=agent_id,
        request_id=request_id,
        custom_data={"candidate_name": "Rishwanth", "company_name": "Acme Logistics"},
    )

    try:
        call = await client.create_call(payload)
    except HunarError as exc:
        fail(f"Call creation failed: {exc}")
        note("A 400 here usually means telephony refused the number or the caller ID.")
        findings["live_call"] = f"error:{exc}"
        return

    ok(f"Call created: {call.id}  status={call.status.value}")
    save("call_created", call.model_dump(mode="json"))
    print("\n  Your phone should ring shortly. Answer it and hold a short conversation.")
    print("  Say a number of years out loud, then say yes or no on interest.\n")

    seen: list[str] = []
    waited = 0
    final = call
    while waited < POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
        try:
            final = await client.get_call(call.id)
        except HunarError as exc:
            fail(f"Polling failed: {exc}")
            break

        if final.status.value not in seen:
            seen.append(final.status.value)
            print(f"    [{waited:>3}s] status -> {final.status.value}")

        if final.is_terminal:
            break

    save("call_final", final.model_dump(mode="json"))
    findings["observed_status_sequence"] = seen
    findings["live_call"] = final.status.value

    heading("5. What the call taught us")

    if final.status is not CallStatus.COMPLETED:
        fail(f"Call ended as {final.status.value} rather than COMPLETED.")
        note("NOT_CONNECTED usually means unanswered. Try again before concluding.")
        return

    ok(f"Call completed in {final.duration_seconds}s")

    if final.recording_url:
        ok("A recording URL is present.")
        note("Download it NOW. These links very likely die with the key.")
        findings["recording_url_present"] = True
    else:
        note("No recording URL yet; it may arrive later via the recording webhook.")
        findings["recording_url_present"] = False

    if not final.result:
        fail("No `result` on the completed call.")
        note("Extraction may be asynchronous, arriving only via call_result_done.")
        findings["result_present"] = False
        return

    ok("Extracted result present:")
    print(json.dumps(final.result, indent=4, ensure_ascii=False))
    findings["result_present"] = True
    findings["result_raw"] = final.result

    # The decisive question for the normaliser: does a field declared
    # "number" or "boolean" come back typed, or as a string?
    types_seen = {key: type(value).__name__ for key, value in final.result.items()}
    findings["result_value_types"] = types_seen
    note(f"Value types: {types_seen}")

    non_strings = {k: v for k, v in types_seen.items() if v not in ("str", "NoneType")}
    if non_strings:
        ok(f"Some values came back natively typed: {non_strings}")
        note("The normaliser must therefore accept BOTH typed and string inputs.")
    else:
        ok("Every value came back as a string, as expected.")
        note("Confirms the string-coercion normaliser is mandatory.")


# ──────────────────────────────────────────────────────────────
async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--call",
        metavar="+91XXXXXXXXXX",
        help="place one real call to this number. Only use a number you own.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required alongside --call, since a real call spends minutes",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="run only the read-only probes",
    )
    args = parser.parse_args()

    load_env_file(REPO_ROOT / ".env")
    api_key = os.environ.get("HUNAR_API_KEY", "").strip()
    base_url = os.environ.get("HUNAR_BASE_URL", "").strip() or None

    print("=" * 68)
    print("  Hunar API spike")
    print(f"  {datetime.now(UTC).isoformat(timespec='seconds')}")
    print("=" * 68)

    if not api_key or api_key.endswith("replace_me"):
        fail("HUNAR_API_KEY is not set.")
        note("Copy .env.example to .env and put the real key in it.")
        note("Never paste the key into a chat, a commit, or this script.")
        return 2

    note(f"key={mask(api_key)}  base_url={base_url or 'default'}")

    findings: dict[str, Any] = {"run_at": datetime.now(UTC).isoformat()}
    client = LiveHunarClient(api_key, base_url=base_url) if base_url else LiveHunarClient(api_key)

    try:
        if not await probe_auth(client, findings):
            return 1

        has_numbers = await probe_numbers(client, findings)

        agent_id: UUID | None = None
        if not args.skip_agent:
            agent_id = await probe_agent_creation(client, findings)

        if args.call:
            if not args.confirm:
                heading("Live call skipped")
                note("--call was given without --confirm, so no call was placed.")
                note("Re-run with both flags once you are ready to spend minutes.")
            elif agent_id is None:
                fail("Cannot place a call without a successfully created agent.")
            elif not has_numbers:
                note("No validated caller ID; attempting anyway to see the exact error.")
                await probe_real_call(client, agent_id, args.call, findings)
            else:
                await probe_real_call(client, agent_id, args.call, findings)
        else:
            heading("Live call not requested")
            note("Re-run with  --call +91XXXXXXXXXX --confirm  to test end to end.")
            note("Use only a number you own.")
    finally:
        await client.aclose()

    save("_findings", findings)
    heading("Summary")
    for key, value in findings.items():
        rendered = json.dumps(value, default=str)
        print(f"  {key:<28} {rendered[:100]}")
    print(f"\n  Fixtures in {OUTPUT_DIR.relative_to(REPO_ROOT)} (gitignored).")
    print("  Copy the ones worth keeping into packages/hunar-sdk/.../fixtures/.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
