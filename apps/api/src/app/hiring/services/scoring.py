"""Transparent, rule-based scoring for shortlisting.

Deliberately not a language-model judge. Three reasons, in order of
weight:

1. **Explainability.** A recruiter deciding who to interview is entitled
   to know exactly why someone scored what they did, field by field. A
   weighted rule produces that for free; a model verdict does not.
2. **Defensibility.** This ranks people for employment. A deterministic
   rule can be audited, reproduced and challenged. An opaque score
   applied to a protected decision is exactly the wrong shape.
3. **Cost and latency.** It is arithmetic. Spending an API call and a
   second of latency per candidate to do it worse would be a poor trade.

Two behaviours matter more than the arithmetic:

* **Unanswered is not failed.** Only questions the candidate actually
  answered count towards the denominator. Someone who answered three of
  five questions well is not punished for a call that ended early, they
  are shown with lower coverage so the recruiter can judge for themselves.
* **Knockouts disqualify visibly.** A hard requirement that is clearly
  failed removes the candidate from the ranked list, but always with the
  reason attached and never silently.
"""

from __future__ import annotations

from typing import Any

from app.hiring.models import AnswerType
from app.hiring.schemas import QuestionOut

__all__ = ["ScoreResult", "score_candidate"]

_MAX_SCORE = 100.0


class ScoreResult:
    """A score, and the complete reasoning behind it."""

    __slots__ = ("breakdown", "coverage", "disqualified", "reason", "score")

    def __init__(
        self,
        *,
        score: float | None,
        breakdown: dict[str, Any],
        disqualified: bool,
        reason: str | None,
        coverage: float,
    ) -> None:
        self.score = score
        self.breakdown = breakdown
        self.disqualified = disqualified
        self.reason = reason
        self.coverage = coverage

    def as_dict(self) -> dict[str, Any]:
        """Flatten into the shape the API and the UI consume.

        ``breakdown`` is spread rather than nested. Nesting it under a
        ``contributions`` key produced ``contributions.contributions``,
        which reads as a list at a glance and is a dict in fact, so the
        UI would have iterated over key names instead of contributions.
        """
        return {
            "score": self.score,
            "coverage": self.coverage,
            "disqualified": self.disqualified,
            "reason": self.reason,
            **self.breakdown,
        }


def _evaluate(value: Any, rule: dict[str, Any] | None, answer_type: AnswerType) -> bool | None:
    """Judge one answer against its rule.

    Returns ``None`` when the answer cannot be judged, which keeps it out
    of the denominator rather than counting as a failure.
    """
    if value is None:
        return None

    if rule is None:
        # With no explicit rule, a boolean scores on being true and any
        # other answered field simply counts as answered.
        if answer_type is AnswerType.BOOLEAN:
            return bool(value)
        return True

    op = rule.get("op")
    target = rule.get("value")

    try:
        match op:
            case "is_true":
                return value is True
            case "is_false":
                return value is False
            case "gte":
                # A comparison rule with no threshold is a malformed rule,
                # not a failed answer, so it stays unjudged.
                return None if target is None else float(value) >= float(target)
            case "lte":
                return None if target is None else float(value) <= float(target)
            case "eq":
                if isinstance(value, str) and isinstance(target, str):
                    return value.strip().lower() == target.strip().lower()
                return bool(value == target)
            case "in":
                options = [str(option).lower() for option in target or []]
                return str(value).strip().lower() in options
            case "not_in":
                options = [str(option).lower() for option in target or []]
                return str(value).strip().lower() not in options
            case "contains":
                return str(target).strip().lower() in str(value).strip().lower()
            case _:
                return None
    except (TypeError, ValueError):
        # A numeric rule against unparseable text, for example. Unjudgeable
        # rather than failed, so it does not silently count against anyone.
        return None


def _describe(question: QuestionOut, value: Any, passed: bool | None) -> str:
    """Explain one contribution in words a recruiter can act on."""
    if value is None:
        return f"{question.label}: not answered"
    if passed is None:
        return f"{question.label}: answered '{value}', could not be assessed"
    verdict = "meets" if passed else "does not meet"
    return f"{question.label}: '{value}' {verdict} the requirement"


def score_candidate(normalized: dict[str, Any] | None, questions: list[QuestionOut]) -> ScoreResult:
    """Score one screened candidate against the job's questions.

    Args:
        normalized: Coerced answers, keyed by field key.
        questions: The job's questions, carrying weights and rules.

    Returns:
        A :class:`ScoreResult` whose breakdown lists every question, what
        the candidate said, and whether it counted. The breakdown is
        rendered directly in the UI, so a recruiter never sees a number
        without its reasoning.
    """
    if not normalized:
        return ScoreResult(
            score=None,
            breakdown={"contributions": [], "note": "No answers were extracted"},
            disqualified=False,
            reason=None,
            coverage=0.0,
        )

    contributions: list[dict[str, Any]] = []
    earned = 0.0
    available = 0.0
    answered = 0
    scorable = 0

    knockout_failures: list[str] = []

    for question in questions:
        value = normalized.get(question.field_key)
        passed = _evaluate(value, question.scoring_rule, question.answer_type)

        if question.weight > 0:
            scorable += 1

        if value is not None:
            answered += 1

        # A knockout only fires on a clear, judged failure. An unanswered
        # or unjudgeable requirement is never treated as a rejection.
        if question.is_knockout and passed is False:
            knockout_failures.append(
                f"{question.label}: '{value}' does not meet a required condition"
            )

        if passed is not None and question.weight > 0:
            available += question.weight
            if passed:
                earned += question.weight

        contributions.append(
            {
                "field_key": question.field_key,
                "label": question.label,
                "value": value,
                "weight": question.weight,
                "passed": passed,
                "is_knockout": question.is_knockout,
                "explanation": _describe(question, value, passed),
            }
        )

    coverage = round(answered / len(questions), 3) if questions else 0.0

    if knockout_failures:
        return ScoreResult(
            score=0.0,
            breakdown={"contributions": contributions, "knockouts": knockout_failures},
            disqualified=True,
            reason="; ".join(knockout_failures),
            coverage=coverage,
        )

    # Denominator is the weight actually assessable, so a short call
    # lowers coverage rather than manufacturing a low score.
    score = round(earned / available * _MAX_SCORE, 1) if available > 0 else None

    return ScoreResult(
        score=score,
        breakdown={
            "contributions": contributions,
            "earned_weight": round(earned, 2),
            "assessed_weight": round(available, 2),
            "questions_answered": answered,
            "questions_scorable": scorable,
        },
        disqualified=False,
        reason=None,
        coverage=coverage,
    )
