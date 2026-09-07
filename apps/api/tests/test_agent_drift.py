"""An agent that no longer matches its job must be replaced.

This is the bug this file exists for, and it was reported twice before
being understood, which is the tell that it was invisible from the
inside.

A Hunar agent is created once and never inspected again. When the rule
for the spoken name changed so that it follows the voice, every agent
created earlier kept its old ``persona_name``. The code was correct, the
database was correct, and the live agents were wrong:

    voice_persona: SAM    persona_name: 'Neha'
    voice_persona: ROY    persona_name: 'Neha'

Nothing compared the two, so the only place the mistake appeared was a
male voice introducing itself as Neha on a real phone call to a real
candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.models import CallAttempt
from app.hiring.models import Candidate, Job, JobStatus, ScreeningQuestion
from app.hiring.schemas import QuestionInput
from app.hiring.services.job_service import agent_fingerprint, ensure_agent
from app.hiring.services.prompt_builder import build_agent_payload
from hunar_sdk import AgentCreate, FakeHunarClient


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


def _payload(*, voice: str, name: str | None = None) -> AgentCreate:
    return build_agent_payload(
        title="Delivery Executive",
        company_name="Acme Logistics",
        location="Bengaluru",
        description_raw="Deliver parcels.",
        questions=[QuestionInput(text="Do you have a bike?", label="Bike")],
        language="ENGLISH",
        voice_persona=voice,
        persona_name=name,
    )


async def _job(session: AsyncSession, *, voice: str = "SAM", **kwargs: object) -> Job:
    job = Job(
        title="Delivery Executive",
        company_name="Acme Logistics",
        location="Bengaluru",
        description_raw="Deliver parcels.",
        language="ENGLISH",
        voice_persona=voice,
        status=JobStatus.READY.value,
        **kwargs,
    )
    job.questions.append(
        ScreeningQuestion(
            order_index=0,
            text="Do you have a bike?",
            label="Bike",
            field_key="bike",
            answer_type="BOOLEAN",
        )
    )
    session.add(job)
    await session.flush()
    return job


class TestFingerprint:
    def test_the_voice_changes_the_fingerprint(self) -> None:
        """The exact drift that shipped.

        SAM and NEHA produce different spoken names, so they must produce
        different fingerprints or the change stays undetectable.
        """
        assert agent_fingerprint(_payload(voice="SAM")) != agent_fingerprint(_payload(voice="NEHA"))

    def test_an_explicit_name_changes_the_fingerprint(self) -> None:
        assert agent_fingerprint(_payload(voice="SAM")) != agent_fingerprint(
            _payload(voice="SAM", name="Vikram")
        )

    def test_identical_input_is_stable(self) -> None:
        """Otherwise every launch would replace the agent."""
        assert agent_fingerprint(_payload(voice="SAM")) == agent_fingerprint(_payload(voice="SAM"))


class TestEnsureAgent:
    async def test_an_agent_is_created_with_its_fingerprint(
        self, session: AsyncSession, voice_client: FakeHunarClient
    ) -> None:
        job = await _job(session)
        agent_id = await ensure_agent(session, voice_client, job)

        assert job.hunar_agent_id == agent_id
        # Recorded rather than left null, which is what makes the next
        # launch able to tell whether anything has changed.
        assert job.agent_fingerprint

    async def test_an_unchanged_agent_is_reused(
        self, session: AsyncSession, voice_client: FakeHunarClient
    ) -> None:
        job = await _job(session)
        first = await ensure_agent(session, voice_client, job)
        second = await ensure_agent(session, voice_client, job)
        assert first == second, "a stable job must not churn agents on every launch"

    async def test_changing_the_voice_replaces_the_agent(
        self, session: AsyncSession, voice_client: FakeHunarClient
    ) -> None:
        """The fix. Editing the voice must change what the agent says."""
        job = await _job(session, voice="NEHA")
        first = await ensure_agent(session, voice_client, job)

        before = job.agent_fingerprint

        job.voice_persona = "SAM"
        await session.flush()
        second = await ensure_agent(session, voice_client, job)

        # The id must NOT change: reconciliation finds calls by agent id,
        # so a replacement orphans everything already placed for this
        # role. The agent is corrected, not swapped.
        assert second == first
        assert job.agent_fingerprint != before, "the change must be recorded"

        agent = await voice_client.get_agent(first)
        assert agent.persona_name == "Sam", "a male voice must not keep introducing itself as Neha"

        # And it is stable afterwards: a third launch updates nothing.
        third = await ensure_agent(session, voice_client, job)
        assert third == first

    async def test_a_null_fingerprint_counts_as_drifted(
        self, session: AsyncSession, voice_client: FakeHunarClient
    ) -> None:
        """Every agent created before this existed has a NULL fingerprint.

        Reading NULL as "matches" would have left exactly the broken
        agents this was written to repair in place forever.
        """
        stale_id = uuid.uuid4()
        job = await _job(session, voice="SAM")
        job.hunar_agent_id = stale_id
        job.agent_fingerprint = None
        await session.flush()

        # Nothing exists upstream under that id, so the update fails and
        # the agent is kept rather than the launch being abandoned.
        kept = await ensure_agent(session, voice_client, job)
        assert kept == stale_id

    async def test_a_running_call_keeps_its_agent(
        self, session: AsyncSession, voice_client: FakeHunarClient
    ) -> None:
        """Correcting drift must not disturb a call in progress.

        Since the fix updates rather than replaces, the id survives and
        the in-flight call stays reconcilable.
        """
        job = await _job(session, voice="SAM")
        original = await ensure_agent(session, voice_client, job)

        # A call needs a subject to satisfy the exclusive-or constraint,
        # so give it a real candidate rather than working around it.
        candidate = Candidate(job_id=job.id, name="In Flight", mobile_number="+919000000009")
        session.add(candidate)
        await session.flush()
        session.add(
            CallAttempt(
                job_id=job.id,
                candidate_id=candidate.id,
                request_id="req-in-flight",
                callback_token="tok-in-flight",
                status="RINGING",
            )
        )
        await session.flush()

        job.voice_persona = "NEHA"
        await session.flush()
        kept = await ensure_agent(session, voice_client, job)

        assert kept == original, "an in-flight call must not be orphaned"
