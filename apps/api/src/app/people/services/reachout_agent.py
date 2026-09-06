"""The cold-outreach voice agent.

This conversation is not a screening call with different words. The
person did not apply, has never heard of the company, and did not agree
to be phoned. Everything below follows from that.

The first turn asks permission, and "no" is a clean exit rather than an
objection to handle. Any prompt that tries to work past a refusal is both
wrong and, under India's unsolicited-communication rules, evidence. The
agent also discloses where the number came from without being asked,
because a stranger's first thought on an unexpected call is "how did you
get my number" and answering it before they ask is the difference between
a recruiter and a nuisance.

Nothing is collected that the person did not volunteer. No email, no
address, no employer confidential detail, nothing about their current
package unless they raise it.
"""

from __future__ import annotations

from typing import Any

from hunar_sdk import AgentCreate, Language, VoicePersona
from hunar_sdk.personas import persona_name_for
from hunar_sdk.sanitize import sanitize, sanitize_custom_data

__all__ = [
    "REACHOUT_FIELD_SPEC",
    "REACHOUT_RESULT_SCHEMA",
    "build_reachout_agent",
    "reachout_custom_data",
    "script_preview",
]

#: Flat map of field to type hint, exactly as Hunar expects. Values come
#: back as strings whatever this says, which is why the shared normaliser
#: runs over them before anything reads them.
REACHOUT_RESULT_SCHEMA: dict[str, str] = {
    "permission_granted": "boolean",
    "interested": "boolean",
    "do_not_contact": "boolean",
    "current_role": "string",
    "current_company": "string",
    "actively_looking": "boolean",
    "notice_period": "string",
    "expected_compensation": "string",
    "preferred_location": "string",
    "open_to_relocate": "boolean",
    "callback_window": "string",
    "objection": "string",
    "call_outcome": "string",
}

#: The richer description the dashboard renders columns from. Same shape
#: as the hiring app's field spec, so the results table is reused whole.
REACHOUT_FIELD_SPEC: list[dict[str, Any]] = [
    {
        "key": "permission_granted",
        "label": "Agreed to talk",
        "answer_type": "BOOLEAN",
        "system": True,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "interested",
        "label": "Interested",
        "answer_type": "BOOLEAN",
        "system": True,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "do_not_contact",
        "label": "Do not contact",
        "answer_type": "BOOLEAN",
        "system": True,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "current_role",
        "label": "Current role",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "current_company",
        "label": "Current company",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "actively_looking",
        "label": "Actively looking",
        "answer_type": "BOOLEAN",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "notice_period",
        "label": "Notice period",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "expected_compensation",
        "label": "Expected pay",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "preferred_location",
        "label": "Preferred location",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "open_to_relocate",
        "label": "Open to relocate",
        "answer_type": "BOOLEAN",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "callback_window",
        "label": "Best time to call",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "objection",
        "label": "Objection",
        "answer_type": "STRING",
        "system": False,
        "weight": 0,
        "is_knockout": False,
    },
    {
        "key": "call_outcome",
        "label": "Outcome",
        "answer_type": "STRING",
        "system": True,
        "weight": 0,
        "is_knockout": False,
    },
]


INTRODUCTION = (
    "Hi, am I speaking with {callee_name}? My name is {persona_name} and I'm "
    "calling on behalf of {company_name} about a {job_title} role in {job_city}. "
    "I found your profile through a professional directory, so this is "
    "completely out of the blue and I'm sorry for the interruption. Is this an "
    "okay moment for a two minute conversation, or should I let you go?"
)

OBJECTIVE = (
    "Get clear permission to continue. If it is given, describe the "
    "{job_title} role at {company_name} in one or two sentences, find out "
    "whether {callee_name} is open to hearing more, and capture only what they "
    "volunteer about their current role, notice period, pay expectation, "
    "preferred location and the best time for a human recruiter to call back. "
    "End the call immediately and politely if permission or interest is declined."
)

AGENT_PROMPT = """You are {persona_name}, a recruiting coordinator calling on \
behalf of {company_name}.

This is a cold call. {callee_name} has not applied for anything, has never \
heard of you, and did not ask to be contacted. Your job is to be brief, \
honest, and very easy to say no to.

ABSOLUTE RULES
1. Ask nothing about their career until they have clearly agreed to continue. \
If the answer to your opening is no, or unclear, or hesitant, say "No problem \
at all, I won't take any more of your time. Have a good day." and end the call.
2. Never pressure. Never pitch again after a no. Never ask twice for the same \
thing. Never imply they applied or that you have spoken before.
3. If they ask how you got their number, answer plainly: "Your professional \
profile is listed in a business directory that we license. I can have you \
removed from our list right now if you would like." If they want that, confirm \
it warmly and end the call.
4. If they mention the Do Not Disturb registry, ask not to be contacted, or \
tell you to stop calling, confirm they will be removed and end immediately.
5. State nothing about the role beyond the facts given below. If asked \
something you were not told, say "I don't have that detail, the recruiter can \
cover it when they call you."
6. Collect nothing they have not volunteered. Do not ask for an email address, \
a home address, a date of birth, their employer's confidential information, or \
anything about their family.
7. Keep every reply under about twenty-five words. Aim to finish inside three \
minutes.
8. If you reach voicemail, leave no details of the role or the pay. Say only: \
"Hi, this is {persona_name} calling for {callee_name} about a professional \
opportunity. Sorry to have missed you."

THE ROLE, WHICH IS THE ONLY THING YOU MAY DESCRIBE
Company: {company_name}
Role: {job_title}
Location and working pattern: {job_city}, {work_mode}
Why it might interest them: {role_pitch}
Compensation, only if they ask or raise it: {comp_range_text}

HOW THE CALL GOES
Step 1. Permission. Already asked in your opening. Wait for a clear yes.
Step 2. Pitch, two sentences at most, using the reason above.
Step 3. Gauge interest: "Is that the kind of move you would be open to hearing \
more about, or is it not the right time?"
Step 4. Only if they are interested, ask these one at a time, skipping any they \
have already answered. Accept "prefer not to say" without following up:
   a) "What are you working on at the moment, role and company?"
   b) "Are you actively looking, or just open to the right thing?"
   c) "If something did work out, what notice period are you on?"
   d) "Do you have a compensation range in mind?"
   e) "Are you looking to stay in {job_city}, or open to elsewhere?"
   f) "When would be a good time for {recruiter_name} to call you back?"
Step 5. Close. If interested: "Thanks {callee_name}, {recruiter_name} will be \
in touch. Have a good day." If not: thank them, confirm you will not call \
again, and end."""

RESULT_PROMPT = """Read the transcript of this cold outreach call and fill in \
the fields below. Use only what the person actually said.

- If something was not discussed, or they declined to answer, return an empty \
string for a text field and false for a boolean. Never guess and never infer \
from tone.
- Set permission_granted true only if they clearly agreed to continue past the \
introduction.
- Set interested true only if they explicitly said they are open to hearing \
more. Politeness is not interest.
- Set do_not_contact true if they asked not to be contacted again, mentioned \
the Do Not Disturb registry, asked to be removed from the list, or were \
plainly annoyed at being called. When in doubt here, choose true: wrongly \
suppressing one prospect costs nothing, wrongly calling someone again is a \
complaint.
- Quote notice period and compensation in the person's own words.
- call_outcome: one short sentence a recruiter can read at a glance."""


def build_reachout_agent(
    *,
    campaign_title: str,
    voice_persona: str = "NEHA",
    language: str = "ENGLISH",
) -> AgentCreate:
    """Build the agent for one outreach campaign."""
    return AgentCreate(
        name=sanitize(f"{campaign_title} outreach", max_length=60) or "outreach agent",
        voice_persona=VoicePersona(voice_persona),
        language=Language(language),
        persona_name=persona_name_for(voice_persona),
        introduction=INTRODUCTION,
        objective=OBJECTIVE,
        agent_prompt=AGENT_PROMPT,
        result_prompt=RESULT_PROMPT,
        result_schema=REACHOUT_RESULT_SCHEMA,
    )


def reachout_custom_data(
    *,
    callee_name: str,
    job_title: str,
    company_name: str,
    job_city: str | None,
    work_mode: str | None,
    role_pitch: str,
    comp_range_text: str | None,
    recruiter_name: str,
    voice_persona: str = "NEHA",
) -> dict[str, str]:
    """Per-prospect values injected into the agent's ``{variables}``.

    Everything is brace-stripped on the way through. A pitch pasted from
    a document containing a brace would otherwise corrupt the sentence
    the agent speaks to a stranger, which is a bad first impression to
    make on someone who did not ask to be called.
    """
    return sanitize_custom_data(
        {
            "callee_name": callee_name,
            "job_title": job_title,
            "company_name": company_name,
            "job_city": job_city or "our office",
            "work_mode": work_mode or "to be discussed",
            "role_pitch": role_pitch,
            "comp_range_text": comp_range_text or "not disclosed at this stage",
            "recruiter_name": recruiter_name,
            "persona_name": persona_name_for(voice_persona),
        }
    )


def script_preview(data: dict[str, str]) -> dict[str, str]:
    """Render the script with real values, for review before calling.

    An operator should be able to read the exact opening sentence a
    stranger will hear, in the words they will hear it, before anyone
    presses the button.
    """

    def fill(template: str) -> str:
        rendered = template
        for key, value in data.items():
            rendered = rendered.replace(f"{{{key}}}", value)
        return rendered

    return {
        "introduction": fill(INTRODUCTION),
        "objective": fill(OBJECTIVE),
        "agent_prompt": fill(AGENT_PROMPT),
        "result_prompt": RESULT_PROMPT,
    }
