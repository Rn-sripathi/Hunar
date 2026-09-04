"""Tests for turning a job description into a voice agent.

The brace-stripping cases matter most. Hunar interpolates prompts with
single-brace ``{variable}`` syntax, so an unescaped brace in a pasted job
description does not raise anything: it silently corrupts a sentence the
agent then speaks aloud to a real candidate.
"""

from __future__ import annotations

import re

import pytest

from app.hiring.models import AnswerType
from app.hiring.schemas import QuestionInput
from app.hiring.services.prompt_builder import (
    SYSTEM_FIELDS,
    assign_field_keys,
    build_agent_payload,
    build_agent_prompt,
    build_field_spec,
    build_preview,
    build_result_prompt,
    build_result_schema,
    slugify_field_key,
)
from hunar_sdk.sanitize import ALLOWED_PROMPT_VARIABLES


def question(label: str, **overrides: object) -> QuestionInput:
    payload: dict[str, object] = {
        "label": label,
        "text": f"Tell me about {label.lower()}?",
        "answer_type": AnswerType.STRING,
    }
    payload.update(overrides)
    return QuestionInput.model_validate(payload)


class TestSlugifyFieldKey:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Years of experience", "years_of_experience"),
            ("Notice period", "notice_period"),
            ("Owns a two-wheeler?", "owns_a_two_wheeler"),
            ("Expected salary (INR)", "expected_salary_inr"),
            ("  Spaced   out  ", "spaced_out"),
        ],
    )
    def test_produces_snake_case(self, label: str, expected: str) -> None:
        assert slugify_field_key(label, set()) == expected

    def test_prefixes_a_leading_digit(self) -> None:
        """A bare number is not a valid identifier in most consumers."""
        assert slugify_field_key("2 wheeler owned", set()).startswith("q_")

    def test_strips_accents_to_ascii(self) -> None:
        assert slugify_field_key("Café location", set()) == "cafe_location"

    def test_suffixes_a_collision_rather_than_failing(self) -> None:
        """A recruiter should not have to think about key uniqueness."""
        taken: set[str] = set()
        first = slugify_field_key("Location", taken)
        second = slugify_field_key("Location", taken)
        assert first == "location"
        assert second == "location_2"

    def test_avoids_reserved_keys(self) -> None:
        """`interested` is a system field; a question must not overwrite it."""
        assert slugify_field_key("Interested", set()) == "interested_answer"

    def test_never_produces_an_empty_key(self) -> None:
        assert slugify_field_key("!!!", set())

    def test_never_collides_with_a_system_field(self) -> None:
        questions = [question("Interested"), question("Summary"), question("Call summary")]
        keys = assign_field_keys(questions)
        system_keys = {key for key, _, _, _ in SYSTEM_FIELDS}
        assert not set(keys) & system_keys
        assert len(set(keys)) == len(keys)


class TestResultSchema:
    def test_maps_answer_types_to_hints(self) -> None:
        questions = [
            question("Experience", answer_type=AnswerType.NUMBER),
            question("Relocate", answer_type=AnswerType.BOOLEAN),
            question("Shift", answer_type=AnswerType.ENUM, enum_options=["Day", "Night"]),
        ]
        schema = build_result_schema(questions, assign_field_keys(questions))
        assert schema["experience"] == "number"
        assert schema["relocate"] == "boolean"
        # ENUM has no Hunar equivalent, so it degrades to string and the
        # allowed values are enforced through result_prompt instead.
        assert schema["shift"] == "string"

    def test_always_includes_the_system_fields(self) -> None:
        """The dashboard needs a consistent spine to sort and filter on."""
        questions = [question("Experience")]
        schema = build_result_schema(questions, assign_field_keys(questions))
        for key, _label, _type, _hint in SYSTEM_FIELDS:
            assert key in schema

    def test_is_accepted_by_the_sdk_model(self) -> None:
        """Keys must satisfy AgentCreate's snake_case validation."""
        questions = [question("Years of experience"), question("Owns a two-wheeler?")]
        payload = build_agent_payload(
            title="Delivery Executive",
            company_name="Acme",
            location="Bengaluru",
            description_raw="Ride safely.",
            questions=questions,
        )
        assert payload.result_schema


class TestResultPrompt:
    def test_states_a_format_rule_per_field(self) -> None:
        """Format rules are the main defence against free-prose answers."""
        questions = [
            question("Experience", answer_type=AnswerType.NUMBER),
            question("Relocate", answer_type=AnswerType.BOOLEAN),
        ]
        prompt = build_result_prompt(questions, assign_field_keys(questions))
        assert "digits only" in prompt
        assert "true or false" in prompt

    def test_lists_enum_options_explicitly(self) -> None:
        questions = [question("Shift", answer_type=AnswerType.ENUM, enum_options=["Day", "Night"])]
        prompt = build_result_prompt(questions, assign_field_keys(questions))
        assert "Day, Night" in prompt

    def test_instructs_against_guessing(self) -> None:
        """The most damaging failure mode is invented answers."""
        questions = [question("Experience")]
        prompt = build_result_prompt(questions, assign_field_keys(questions))
        assert "never infer" in prompt.lower() or "never guess" in prompt.lower()

    def test_includes_the_extraction_hint(self) -> None:
        questions = [question("Salary", extraction_hint="Record the monthly figure.")]
        prompt = build_result_prompt(questions, assign_field_keys(questions))
        assert "monthly figure" in prompt

    def test_strips_braces_from_hints(self) -> None:
        questions = [question("Salary", extraction_hint="Between {20000} and {30000}")]
        prompt = build_result_prompt(questions, assign_field_keys(questions))
        assert "{" not in prompt.split("Fields:")[1]


class TestAgentPrompt:
    def test_strips_braces_from_the_job_description(self) -> None:
        """The single most important test in this module.

        A brace pasted from a JSON snippet or a salary note would
        otherwise be read as a template variable and corrupt what the
        agent says on a live call.
        """
        prompt = build_agent_prompt(
            title="Backend Engineer",
            company_name="Acme",
            location="Pune",
            description_raw='Use {"stack": "python"} and earn {35-50 LPA}',
            questions=[question("Experience")],
            language="ENGLISH",
        )
        # The pasted braces are gone, and their contents survive as text.
        assert '{"stack"' not in prompt
        assert "{35-50 LPA}" not in prompt
        assert "35-50 LPA" in prompt

        # The only braces left are variables we deliberately declared.
        # This is the property that actually matters: a stray brace would
        # be interpolated, and anything unrecognised would be spoken as-is.
        found = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", prompt))
        assert found <= ALLOWED_PROMPT_VARIABLES, f"undeclared variables: {found}"
        assert "{" not in re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "", prompt)

    def test_strips_braces_from_question_text(self) -> None:
        prompt = build_agent_prompt(
            title="Rider",
            company_name="Acme",
            location=None,
            description_raw="",
            questions=[question("Pay", text="Is {40000} acceptable?")],
            language="ENGLISH",
        )
        assert "{40000}" not in prompt

    def test_keeps_the_intended_template_variables(self) -> None:
        """Stripping must not remove the placeholders we rely on."""
        prompt = build_agent_prompt(
            title="Rider",
            company_name="Acme",
            location="Delhi",
            description_raw="Deliver parcels.",
            questions=[question("Experience")],
            language="ENGLISH",
        )
        for variable in ("{candidate_name}", "{company_name}", "{job_title}"):
            assert variable in prompt

    def test_forbids_promising_pay_or_outcomes(self) -> None:
        """The agent talks to people about their livelihood."""
        prompt = build_agent_prompt(
            title="Rider",
            company_name="Acme",
            location=None,
            description_raw="",
            questions=[question("Experience")],
            language="ENGLISH",
        )
        lowered = prompt.lower()
        assert "never promise" in lowered
        assert "not interested" in lowered

    def test_numbers_the_questions_in_order(self) -> None:
        questions = [question("First"), question("Second"), question("Third")]
        prompt = build_agent_prompt(
            title="Rider",
            company_name="Acme",
            location=None,
            description_raw="",
            questions=questions,
            language="ENGLISH",
        )
        assert prompt.index("1. ") < prompt.index("2. ") < prompt.index("3. ")

    def test_truncates_an_enormous_job_description(self) -> None:
        """Prompt budget is finite and some pasted descriptions are huge."""
        prompt = build_agent_prompt(
            title="Rider",
            company_name="Acme",
            location=None,
            description_raw="word " * 5000,
            questions=[question("Experience")],
            language="ENGLISH",
        )
        assert len(prompt) < 8000


class TestPreviewAndPayload:
    def test_preview_reports_the_variables_it_uses(self) -> None:
        """The create-job screen shows these so a missing value is visible."""
        preview = build_preview(
            title="Rider",
            company_name="Acme",
            location="Delhi",
            description_raw="Deliver parcels.",
            questions=[question("Experience")],
            language="ENGLISH",
        )
        assert "candidate_name" in preview.variables
        assert "company_name" in preview.variables

    def test_is_deterministic(self) -> None:
        """Identical input must produce an identical agent.

        Otherwise the script previewed before launching would not be the
        script the candidate actually hears.
        """
        kwargs = {
            "title": "Rider",
            "company_name": "Acme",
            "location": "Delhi",
            "description_raw": "Deliver parcels.",
            "questions": [question("Experience"), question("Relocate")],
            "language": "ENGLISH",
        }
        assert build_preview(**kwargs) == build_preview(**kwargs)  # type: ignore[arg-type]

    def test_truncates_a_long_agent_name(self) -> None:
        """Hunar caps the name at 64 characters and rejects longer ones."""
        payload = build_agent_payload(
            title="Senior Regional Field Operations Delivery Excellence Manager" * 2,
            company_name="Acme",
            location=None,
            description_raw="",
            questions=[question("Experience")],
        )
        assert len(payload.name) <= 64

    def test_field_spec_marks_system_columns(self) -> None:
        questions = [question("Experience", weight=2.0, is_knockout=True)]
        spec = build_field_spec(questions, assign_field_keys(questions))

        recruiter_fields = [field for field in spec if not field.system]
        assert len(recruiter_fields) == 1
        assert recruiter_fields[0].weight == 2.0
        assert recruiter_fields[0].is_knockout is True
        assert any(field.system for field in spec)

    def test_field_spec_covers_every_schema_key(self) -> None:
        """Columns and extracted keys must agree or answers go unrendered."""
        questions = [question("Experience"), question("Relocate")]
        keys = assign_field_keys(questions)
        schema = build_result_schema(questions, keys)
        spec = build_field_spec(questions, keys)
        assert {field.key for field in spec} == set(schema)
