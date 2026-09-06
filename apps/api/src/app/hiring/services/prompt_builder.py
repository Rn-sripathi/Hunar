"""Turns a job description and a question list into a voice agent.

This is the translation at the heart of the product, and it is shaped
almost entirely by one API constraint: ``result_schema`` is a flat map of
field name to a type-hint string. It has no place for a description, no
enums, no nesting, and no validation. So the schema carries only the
shape, and every instruction about *how* to extract an answer has to be
written into ``result_prompt`` instead.

The second constraint is that Hunar interpolates ``custom_data`` into
prompts using single-brace ``{variable}`` syntax. Recruiters paste job
descriptions containing braces regularly, from JSON snippets to salary
notes. An unescaped brace does not raise; it silently corrupts a sentence
the agent then speaks aloud to a candidate. Every piece of operator text
is therefore stripped of braces before it reaches a template.

Everything here is a pure function. Given the same job and questions it
produces the same agent, which is what lets the create-job screen show a
recruiter the exact script before a single call is placed.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.hiring.models import AnswerType
from app.hiring.schemas import AgentPreview, FieldSpec, QuestionInput
from hunar_sdk import AgentCreate, Language, VoicePersona
from hunar_sdk.personas import PERSONA_NAMES, persona_name_for
from hunar_sdk.sanitize import sanitize, sanitize_custom_data

__all__ = [
    "PERSONA_NAMES",
    "SYSTEM_FIELDS",
    "build_agent_payload",
    "build_agent_prompt",
    "build_field_spec",
    "build_preview",
    "build_result_prompt",
    "build_result_schema",
    "persona_name_for",
    "slugify_field_key",
]

#: Reserved because they either collide with our own system fields or read
#: badly as a column header.
_RESERVED_KEYS = frozenset(
    {
        "id",
        "status",
        "result",
        "summary",
        "call_summary",
        "interested",
        "candidate_confirmed",
        "score",
        "class",
        "type",
    }
)

#: Present on every job regardless of what the recruiter asked, so the
#: dashboard always has a consistent spine to sort and filter on.
SYSTEM_FIELDS: tuple[tuple[str, str, AnswerType, str], ...] = (
    (
        "interested",
        "Interested",
        AnswerType.BOOLEAN,
        "true if the candidate expressed willingness to proceed with this role, "
        "false if they declined or were not interested",
    ),
    (
        "candidate_confirmed",
        "Identity confirmed",
        AnswerType.BOOLEAN,
        "true if the person on the call confirmed they are the candidate named",
    ),
    (
        "call_summary",
        "Summary",
        AnswerType.STRING,
        "one or two sentences summarising the candidate's suitability, in plain English",
    ),
)

_MAX_JD_CHARS = 3500
_MAX_KEY_WORDS = 5
_MAX_KEY_CHARS = 40


def slugify_field_key(text: str, taken: set[str]) -> str:
    """Derive a stable snake_case key from a question label.

    The key becomes a ``result_schema`` entry, a database column value and
    a dashboard column id, so it has to be a safe identifier and unique
    within the job. Collisions are suffixed rather than rejected, because
    a recruiter should not have to think about identifier uniqueness.
    """
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)

    if not slug:
        slug = "field"
    if slug[0].isdigit():
        slug = f"q_{slug}"

    slug = "_".join(slug.split("_")[:_MAX_KEY_WORDS])[:_MAX_KEY_CHARS].strip("_")
    if slug in _RESERVED_KEYS:
        slug = f"{slug}_answer"

    base, suffix = slug, 2
    while slug in taken:
        slug = f"{base}_{suffix}"
        suffix += 1

    taken.add(slug)
    return slug


def assign_field_keys(questions: list[QuestionInput]) -> list[str]:
    """Allocate one unique key per question, in order."""
    taken: set[str] = {key for key, _, _, _ in SYSTEM_FIELDS}
    return [slugify_field_key(question.label, taken) for question in questions]


def build_result_schema(questions: list[QuestionInput], keys: list[str]) -> dict[str, str]:
    """Build the flat map Hunar extracts against.

    Values are type hints rather than descriptions. Whether Hunar reads
    them at all is unconfirmed, so this is written to be correct either
    way: the hint is accurate if it is used, and the real instruction sits
    in ``result_prompt`` if it is not.
    """
    type_hint = {
        AnswerType.BOOLEAN: "boolean",
        AnswerType.NUMBER: "number",
        AnswerType.STRING: "string",
        AnswerType.ENUM: "string",
    }

    schema = {
        key: type_hint[question.answer_type] for key, question in zip(keys, questions, strict=True)
    }
    for key, _label, answer_type, _hint in SYSTEM_FIELDS:
        schema[key] = type_hint[answer_type]
    return schema


def build_result_prompt(questions: list[QuestionInput], keys: list[str]) -> str:
    """Write the extraction instructions.

    Carries the entire burden of extraction quality, since the schema has
    nowhere to record what a field means. Each field gets an explicit
    format rule, because a free-prose answer where a category was wanted
    is the most common way structured extraction degrades.
    """
    lines = [
        "Read the transcript of this call and extract exactly the fields listed below.",
        "",
        "Rules that apply to every field:",
        "- Use only what the candidate actually said. Never infer, guess or embellish.",
        "- If a field was not discussed, or the candidate declined to answer, "
        "return an empty string for it.",
        "- Do not add fields that are not listed.",
        "",
        "Fields:",
    ]

    for key, question in zip(keys, questions, strict=True):
        rule = {
            AnswerType.BOOLEAN: "answer only true or false",
            AnswerType.NUMBER: "answer with digits only, no units and no words",
            AnswerType.STRING: "answer with a short phrase in the candidate's own words",
            AnswerType.ENUM: (
                "answer with exactly one of: " + ", ".join(question.enum_options or [])
                if question.enum_options
                else "answer with a single short phrase"
            ),
        }[question.answer_type]

        line = f'- "{key}": the answer to "{sanitize(question.text)}". Format: {rule}.'
        if question.extraction_hint:
            line += f" {sanitize(question.extraction_hint)}"
        lines.append(line)

    for key, _label, _answer_type, hint in SYSTEM_FIELDS:
        lines.append(f'- "{key}": {hint}.')

    return "\n".join(lines)


def build_agent_prompt(
    *,
    title: str,
    company_name: str,
    location: str | None,
    description_raw: str,
    questions: list[QuestionInput],
    language: str,
) -> str:
    """Write the agent's standing instructions.

    The rules are as much about what the agent must not do as what it
    should. It is talking to real people about their livelihood, so it is
    told explicitly never to invent pay details, never to promise an
    outcome, and to end politely the moment someone is not interested.
    """
    jd = sanitize(description_raw, max_length=_MAX_JD_CHARS)
    numbered = "\n".join(
        f"{index}. {sanitize(question.text)}" for index, question in enumerate(questions, start=1)
    )
    where = sanitize(location) or "the role's location"

    jd_block = (
        f"ROLE BACKGROUND (for answering questions only, never read aloud):\n{jd}\n\n" if jd else ""
    )

    return f"""You are {{persona_name}}, a recruitment screening assistant calling on \
behalf of {{company_name}} about the {{job_title}} role in {where}.

{jd_block}YOUR TASK
Confirm you are speaking with {{candidate_name}}. Explain the role in one sentence, \
then ask the screening questions below one at a time, in order, waiting for an answer \
before moving on.

SCREENING QUESTIONS
{numbered}

HOW TO SPEAK
- Ask one question at a time. Never read the list out in one go.
- Keep every reply under 30 words. Speak naturally in {language.title()}.
- If the candidate answers in a different language or mixes languages, follow their lead.
- If an answer is vague, ask one clarifying follow-up, then move on.

WHAT YOU MUST NOT DO
- Never promise a salary, a start date, an interview or an offer.
- Never state anything about pay or benefits that is not in the role background above.
- Never pressure the candidate, and never ask the same question twice.
- If you are asked something you do not know, say a human recruiter will follow up.

ENDING THE CALL
- If the candidate is busy, offer to call back later and end politely.
- If the candidate is not interested, thank them warmly and end. Do not re-pitch.
- Otherwise thank them, say a recruiter will be in touch, and end the call."""


def build_introduction(title: str, company_name: str) -> str:
    """The opening line, which has to work on someone who has forgotten applying."""
    return (
        "Hello, am I speaking with {candidate_name}? My name is {persona_name} and "
        "I'm calling from {company_name} about your application for the {job_title} "
        "role. Is now a good time for a few quick questions?"
    )


def build_objective(title: str, questions: list[QuestionInput]) -> str:
    return (
        f"Screen {{candidate_name}} for the {{job_title}} role by asking "
        f"{len(questions)} question(s), and determine whether they are interested "
        "and suitable enough for a human recruiter to follow up."
    )


def build_field_spec(questions: list[QuestionInput], keys: list[str]) -> list[FieldSpec]:
    """Describe the results table's columns.

    Returned to the frontend with every results payload, which is how one
    table renders every job's differently shaped answers.
    """
    spec = [
        FieldSpec(
            key=key,
            label=question.label,
            answer_type=question.answer_type,
            enum_options=question.enum_options,
            weight=question.weight,
            is_knockout=question.is_knockout,
            system=False,
        )
        for key, question in zip(keys, questions, strict=True)
    ]
    spec.extend(
        FieldSpec(
            key=key,
            label=label,
            answer_type=answer_type,
            weight=0.0,
            is_knockout=False,
            system=True,
        )
        for key, label, answer_type, _hint in SYSTEM_FIELDS
    )
    return spec


def build_preview(
    *,
    title: str,
    company_name: str,
    location: str | None,
    description_raw: str,
    questions: list[QuestionInput],
    language: str,
) -> AgentPreview:
    """Assemble everything the agent will be given, for review before calling."""
    keys = assign_field_keys(questions)
    agent_prompt = build_agent_prompt(
        title=title,
        company_name=company_name,
        location=location,
        description_raw=description_raw,
        questions=questions,
        language=language,
    )
    result_prompt = build_result_prompt(questions, keys)

    variables = sorted(
        set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", agent_prompt))
        | set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", build_introduction(title, company_name)))
    )

    return AgentPreview(
        introduction=build_introduction(title, company_name),
        objective=build_objective(title, questions),
        agent_prompt=agent_prompt,
        result_prompt=result_prompt,
        result_schema=build_result_schema(questions, keys),
        variables=variables,
    )


def build_agent_payload(
    *,
    title: str,
    company_name: str,
    location: str | None,
    description_raw: str,
    questions: list[QuestionInput],
    language: str = "ENGLISH",
    voice_persona: str = "NEHA",
    persona_name: str | None = None,
) -> AgentCreate:
    """Build the exact body sent to ``POST /agents/``."""
    preview = build_preview(
        title=title,
        company_name=company_name,
        location=location,
        description_raw=description_raw,
        questions=questions,
        language=language,
    )

    # Hunar caps the agent name at 64 characters, and a long job title
    # would otherwise fail validation after the whole prompt was built.
    safe_name = sanitize(f"{title} screening", max_length=60) or "screening agent"

    return AgentCreate(
        name=safe_name,
        voice_persona=VoicePersona(voice_persona),
        language=Language(language),
        persona_name=persona_name_for(voice_persona, persona_name),
        introduction=preview.introduction,
        objective=preview.objective,
        agent_prompt=preview.agent_prompt,
        result_prompt=preview.result_prompt,
        result_schema=preview.result_schema,
    )


def custom_data_for(
    *,
    candidate_name: str,
    job_title: str,
    company_name: str,
    persona_name: str | None,
    voice_persona: str = "NEHA",
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Per-candidate values injected into the agent's ``{variables}``.

    ``persona_name`` is derived from the voice by the same rule used when
    the agent was created, so the name spoken on the call and the name in
    the stored prompt cannot disagree.
    """
    data: dict[str, Any] = {
        "candidate_name": candidate_name,
        "job_title": job_title,
        "company_name": company_name,
        "persona_name": persona_name_for(voice_persona, persona_name),
    }
    if extra:
        data.update(extra)
    return sanitize_custom_data(data)
