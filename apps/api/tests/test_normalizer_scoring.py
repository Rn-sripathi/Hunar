"""Tests for answer coercion and shortlist scoring.

These two modules decide what a recruiter sees and who gets called back,
so the cases below lean on the failure directions that would matter to a
real candidate: an unparseable answer must never read as a "no", and a
short call must never look like a bad one.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.hiring.models import AnswerType
from app.hiring.schemas import FieldSpec, QuestionOut
from app.hiring.services.result_normalizer import coerce_value, normalize_result
from app.hiring.services.scoring import score_candidate


def spec(key: str, answer_type: AnswerType, options: list[str] | None = None) -> FieldSpec:
    return FieldSpec(key=key, label=key.title(), answer_type=answer_type, enum_options=options)


def outq(
    key: str,
    *,
    answer_type: AnswerType = AnswerType.STRING,
    weight: float = 1.0,
    is_knockout: bool = False,
    rule: dict[str, Any] | None = None,
    options: list[str] | None = None,
) -> QuestionOut:
    return QuestionOut(
        id=uuid.uuid4(),
        order_index=0,
        text=f"Question about {key}",
        label=key.replace("_", " ").title(),
        field_key=key,
        answer_type=answer_type,
        enum_options=options,
        extraction_hint=None,
        weight=weight,
        is_knockout=is_knockout,
        scoring_rule=rule,
    )


class TestBooleanCoercion:
    @pytest.mark.parametrize(
        "raw",
        ["Yes", "yes", "YES", "true", "1", "yeah", "sure", "ok", "correct", "definitely"],
    )
    def test_reads_english_affirmatives(self, raw: str) -> None:
        assert coerce_value(raw, AnswerType.BOOLEAN) is True

    @pytest.mark.parametrize("raw", ["Haan", "haan ji", "ji", "sahi", "हाँ", "ஆம்", "అవును"])
    def test_reads_indian_language_affirmatives(self, raw: str) -> None:
        """Screening runs in Hindi, Tamil and Telugu, not only English."""
        assert coerce_value(raw, AnswerType.BOOLEAN) is True

    @pytest.mark.parametrize("raw", ["No", "nope", "false", "0", "nahi", "नहीं", "இல்லை"])
    def test_reads_negatives(self, raw: str) -> None:
        assert coerce_value(raw, AnswerType.BOOLEAN) is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Yes, I am", True),
            ("yes sir", True),
            ("haan ji bilkul", True),
            ("No, not right now", False),
            ("nahi sir", False),
            ("no, I cannot", False),
        ],
    )
    def test_reads_answers_inside_a_phrase(self, raw: str, expected: bool) -> None:
        assert coerce_value(raw, AnswerType.BOOLEAN) is expected

    def test_a_negation_outweighs_an_affirmative(self) -> None:
        """ "Yes but no" and "haan nahi" both mean no in real speech."""
        assert coerce_value("yes but no", AnswerType.BOOLEAN) is False

    @pytest.mark.parametrize(
        "raw",
        [
            "maybe",
            "I'm not sure",
            "depends",
            "hmm",
            "pata nahi",
            "I don't know yet",
            "let me think about it",
        ],
    )
    def test_unparseable_becomes_none_not_false(self, raw: str) -> None:
        """The most important case in this file.

        Reading an ambiguous answer as "no" would silently reject a
        candidate on a parsing failure rather than on what they said.
        """
        assert coerce_value(raw, AnswerType.BOOLEAN) is None

    @pytest.mark.parametrize(
        "raw", ["", "  ", "N/A", "not discussed", "declined", "prefer not to say", None]
    )
    def test_absent_answers_become_none(self, raw: str | None) -> None:
        assert coerce_value(raw, AnswerType.BOOLEAN) is None


class TestNumberCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("3", 3.0),
            ("3 years", 3.0),
            ("about 5 years", 5.0),
            ("2.5", 2.5),
            ("15,000", 15000.0),
            ("-1", -1.0),
        ],
    )
    def test_extracts_digits_from_speech(self, raw: str, expected: float) -> None:
        assert coerce_value(raw, AnswerType.NUMBER) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("12 LPA", 1_200_000.0), ("3.5 lakh", 350_000.0), ("1 crore", 10_000_000.0)],
    )
    def test_understands_indian_pay_units(self, raw: str, expected: float) -> None:
        """ "12 LPA" and "1200000" are the same answer.

        A results table that sorts them as 12 and 1200000 is worse than
        useless, because it silently ranks candidates by phrasing.
        """
        assert coerce_value(raw, AnswerType.NUMBER) == expected

    @pytest.mark.parametrize(("raw", "expected"), [("three", 3.0), ("five", 5.0), ("do", 2.0)])
    def test_understands_spoken_words(self, raw: str, expected: float) -> None:
        assert coerce_value(raw, AnswerType.NUMBER) == expected

    def test_immediate_notice_is_zero(self) -> None:
        assert coerce_value("immediate", AnswerType.NUMBER) == 0.0

    def test_unparseable_becomes_none(self) -> None:
        assert coerce_value("quite a while", AnswerType.NUMBER) is None


class TestEnumCoercion:
    def test_matches_an_option_exactly(self) -> None:
        assert coerce_value("Night", AnswerType.ENUM, ["Day", "Night"]) == "Night"

    def test_matches_case_insensitively(self) -> None:
        assert coerce_value("night", AnswerType.ENUM, ["Day", "Night"]) == "Night"

    def test_matches_inside_a_phrase(self) -> None:
        assert coerce_value("I prefer night shift", AnswerType.ENUM, ["Day", "Night"]) == "Night"

    def test_keeps_an_unexpected_answer_visible(self) -> None:
        """Falling back to the raw text beats dropping a real answer."""
        assert coerce_value("Rotational", AnswerType.ENUM, ["Day", "Night"]) == "Rotational"


class TestNormalizeResult:
    def test_coerces_every_declared_field(self) -> None:
        field_spec = [
            spec("years_experience", AnswerType.NUMBER),
            spec("relocate", AnswerType.BOOLEAN),
            spec("notice", AnswerType.STRING),
        ]
        result = normalize_result(
            {"years_experience": "4 years", "relocate": "Haan", "notice": "30 days"},
            field_spec,
        )
        assert result == {"years_experience": 4.0, "relocate": True, "notice": "30 days"}

    def test_includes_declared_fields_that_are_missing(self) -> None:
        """Columns come from the same spec, so every column needs a cell."""
        field_spec = [spec("a", AnswerType.STRING), spec("b", AnswerType.STRING)]
        result = normalize_result({"a": "present"}, field_spec)
        assert result == {"a": "present", "b": None}

    def test_drops_fields_the_model_invented(self) -> None:
        field_spec = [spec("a", AnswerType.STRING)]
        result = normalize_result({"a": "x", "hallucinated": "y"}, field_spec)
        assert result == {"a": "x"}

    def test_handles_an_empty_payload(self) -> None:
        assert normalize_result(None, [spec("a", AnswerType.STRING)]) == {}


class TestScoring:
    def test_scores_a_perfect_candidate(self) -> None:
        questions = [
            outq("experience", answer_type=AnswerType.NUMBER, rule={"op": "gte", "value": 2}),
            outq("relocate", answer_type=AnswerType.BOOLEAN, rule={"op": "is_true"}),
        ]
        result = score_candidate({"experience": 5.0, "relocate": True}, questions)
        assert result.score == 100.0
        assert result.disqualified is False

    def test_scores_a_partial_match(self) -> None:
        questions = [
            outq("experience", answer_type=AnswerType.NUMBER, rule={"op": "gte", "value": 5}),
            outq("relocate", answer_type=AnswerType.BOOLEAN, rule={"op": "is_true"}),
        ]
        result = score_candidate({"experience": 1.0, "relocate": True}, questions)
        assert result.score == 50.0

    def test_respects_weights(self) -> None:
        questions = [
            outq("critical", answer_type=AnswerType.BOOLEAN, weight=3.0, rule={"op": "is_true"}),
            outq("minor", answer_type=AnswerType.BOOLEAN, weight=1.0, rule={"op": "is_true"}),
        ]
        result = score_candidate({"critical": True, "minor": False}, questions)
        assert result.score == 75.0

    def test_an_unanswered_question_does_not_count_against_the_candidate(self) -> None:
        """A call that ended early must not read as a bad candidate.

        Only assessable questions form the denominator, so someone who
        answered one question well scores 100 with low coverage rather
        than 50 with full coverage.
        """
        questions = [
            outq("answered", answer_type=AnswerType.BOOLEAN, rule={"op": "is_true"}),
            outq("unanswered", answer_type=AnswerType.BOOLEAN, rule={"op": "is_true"}),
        ]
        result = score_candidate({"answered": True, "unanswered": None}, questions)
        assert result.score == 100.0
        assert result.coverage == 0.5

    def test_reports_coverage_so_a_short_call_is_visible(self) -> None:
        questions = [outq(f"q{index}") for index in range(4)]
        result = score_candidate({"q0": "a", "q1": "b", "q2": None, "q3": None}, questions)
        assert result.coverage == 0.5

    def test_a_knockout_failure_disqualifies_with_a_reason(self) -> None:
        questions = [
            outq(
                "age_ok",
                answer_type=AnswerType.BOOLEAN,
                is_knockout=True,
                rule={"op": "is_true"},
            ),
            outq("experience", answer_type=AnswerType.NUMBER, rule={"op": "gte", "value": 1}),
        ]
        result = score_candidate({"age_ok": False, "experience": 5.0}, questions)
        assert result.disqualified is True
        assert result.reason is not None
        assert "Age Ok" in result.reason

    def test_an_unanswered_knockout_does_not_disqualify(self) -> None:
        """Refusing someone for a question that was never asked is indefensible."""
        questions = [
            outq(
                "age_ok",
                answer_type=AnswerType.BOOLEAN,
                is_knockout=True,
                rule={"op": "is_true"},
            )
        ]
        result = score_candidate({"age_ok": None}, questions)
        assert result.disqualified is False

    def test_an_unjudgeable_rule_does_not_disqualify(self) -> None:
        """A numeric rule against unparseable text is unknown, not failed."""
        questions = [
            outq(
                "experience",
                answer_type=AnswerType.NUMBER,
                is_knockout=True,
                rule={"op": "gte", "value": 3},
            )
        ]
        result = score_candidate({"experience": None}, questions)
        assert result.disqualified is False

    def test_explains_every_contribution(self) -> None:
        """A recruiter must never see a score without its reasoning."""
        questions = [
            outq("experience", answer_type=AnswerType.NUMBER, rule={"op": "gte", "value": 2}),
            outq("relocate", answer_type=AnswerType.BOOLEAN, rule={"op": "is_true"}),
        ]
        result = score_candidate({"experience": 5.0, "relocate": False}, questions)

        contributions = result.breakdown["contributions"]
        assert len(contributions) == 2
        for contribution in contributions:
            assert contribution["explanation"]
            assert "label" in contribution
            assert "value" in contribution

    def test_handles_a_call_with_no_answers(self) -> None:
        result = score_candidate({}, [outq("experience")])
        assert result.score is None
        assert result.disqualified is False

    @pytest.mark.parametrize(
        ("rule", "value", "expected_pass"),
        [
            ({"op": "gte", "value": 2}, 3.0, True),
            ({"op": "gte", "value": 2}, 1.0, False),
            ({"op": "lte", "value": 30}, 15.0, True),
            ({"op": "eq", "value": "Night"}, "night", True),
            ({"op": "in", "value": ["Day", "Night"]}, "Night", True),
            ({"op": "not_in", "value": ["Day"]}, "Night", True),
            ({"op": "contains", "value": "python"}, "I know Python well", True),
            ({"op": "is_false"}, False, True),
        ],
    )
    def test_rule_operators(self, rule: dict[str, Any], value: Any, expected_pass: bool) -> None:
        questions = [outq("field", answer_type=AnswerType.STRING, rule=rule)]
        result = score_candidate({"field": value}, questions)
        assert (result.score == 100.0) is expected_pass

    def test_a_zero_weight_question_is_informational_only(self) -> None:
        """Some answers are worth capturing without affecting the ranking."""
        questions = [
            outq("scored", answer_type=AnswerType.BOOLEAN, weight=1.0, rule={"op": "is_true"}),
            outq("info", answer_type=AnswerType.BOOLEAN, weight=0.0, rule={"op": "is_true"}),
        ]
        result = score_candidate({"scored": True, "info": False}, questions)
        assert result.score == 100.0
