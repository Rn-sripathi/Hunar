"""Turn a pasted job description into a filled-in role.

A recruiter already has the job description. Asking them to retype the
title, the company, the city and then invent five screening questions is
asking them to do work the text already contains. This reads it once and
fills the whole form, leaving them to correct rather than compose.

Two paths, and the fallback matters as much as the main one:

* **Model extraction** reads the description properly, understands that
  "own two-wheeler required" is a hard requirement rather than a
  nice-to-have, and writes questions in the recruiter's register.
* **Heuristic extraction** runs when there is no API key or the call
  fails. It is keyword matching over Indian frontline hiring vocabulary.
  Cruder, but it never leaves the form empty, and every field it produces
  is editable.

The output is a *draft*, never a commitment. Nothing is saved, and the
recruiter sees every field before anything is created.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.hiring.models import AnswerType
from app.hiring.schemas import QuestionInput

logger = structlog.get_logger(__name__)

__all__ = ["JobDraft", "extract_job_draft", "heuristic_draft"]

_MAX_JD_CHARS = 12_000
_LLM_TIMEOUT_SECONDS = 60.0


class DraftQuestion(BaseModel):
    """One screening question the model proposed."""

    label: str = Field(description="Short column heading, two or three words")
    text: str = Field(description="The question as the agent should speak it")
    answer_type: Literal["STRING", "NUMBER", "BOOLEAN", "ENUM"]
    enum_options: list[str] = Field(
        default_factory=list,
        description="Allowed answers. Only for ENUM, otherwise empty.",
    )
    weight: float = Field(description="Importance from 0 to 5")
    is_knockout: bool = Field(
        description="True only for genuine hard requirements stated in the description"
    )
    minimum: float | None = Field(
        default=None,
        description=(
            "For a NUMBER question only. Copy the exact threshold the description "
            "states, so 'minimum 1 year of experience' gives 1 and 'at least 3 years' "
            "gives 3. Use null when the description states no threshold. Never use 0 "
            "as a stand-in for 'no threshold', because a minimum of zero excludes "
            "nobody. Always null for every other answer type."
        ),
    )


class JobDraft(BaseModel):
    """Everything the create-role form needs, derived from the description."""

    title: str
    company_name: str
    location: str
    language: str
    voice_persona: str
    questions: list[DraftQuestion]

    #: How this draft was produced, so the UI can be honest about it.
    source: Literal["model", "heuristic"] = "heuristic"
    note: str | None = None


_SYSTEM = """\
You prepare phone screening interviews for frontline hiring in India: \
delivery riders, warehouse staff, retail assistants, security guards, \
telecallers, field technicians.

From a job description, produce the details of the role and the questions \
an AI voice agent should ask each applicant.

Writing the questions is the part that matters. Rules:

- Ask only what genuinely filters candidates for THIS role. Four to six \
questions is right. A long call loses people.
- Write them as a person would say them out loud on the phone. Short, \
plain, one idea each. Not "Please indicate your total years of relevant \
professional experience" but "How many years of delivery experience do \
you have?".
- Choose the answer type honestly. BOOLEAN for a yes-or-no, NUMBER when \
you want to sort or compare, ENUM when there is a small fixed set of \
answers, STRING otherwise.
- Mark is_knockout true ONLY for requirements the description states as \
mandatory, such as owning a vehicle or holding a licence. A knockout \
removes someone from the list, so err towards false.
- Weight by how much the answer should influence ranking. A stated \
requirement is 3, useful signal is 2, nice to know is 1, informational is 0.
- Never ask about age, gender, marital status, caste, religion or \
anything else unrelated to doing the job.

For the other fields:
- title and company_name: take from the description. If the company is \
not named, use "the company".
- location: the city. Empty string if none is given.
- language: the language the CALL should be conducted in, as one of \
ENGLISH, HINDI, TAMIL, TELUGU, KANNADA, MARATHI, MALAYALAM, GUJARATI, \
BENGALI. Infer from the city when the description does not say. Most \
frontline hiring in India runs in Hindi or the state language rather \
than English.
- voice_persona: one of NEHA, ROY, ZOE, SAM, MIRA, EESHA. Any is fine.
"""


# ── heuristic fallback ───────────────────────────────────────
_CITIES = (
    "Bengaluru",
    "Bangalore",
    "Mumbai",
    "Delhi",
    "New Delhi",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Ahmedabad",
    "Jaipur",
    "Surat",
    "Lucknow",
    "Gurugram",
    "Gurgaon",
    "Noida",
    "Indore",
    "Kochi",
    "Coimbatore",
    "Nagpur",
    "Chandigarh",
    "Bhopal",
    "Patna",
    "Vadodara",
    "Visakhapatnam",
    "Thane",
)

#: City to the language a frontline call would realistically be held in.
_CITY_LANGUAGE = {
    "bengaluru": "KANNADA",
    "bangalore": "KANNADA",
    "chennai": "TAMIL",
    "coimbatore": "TAMIL",
    "hyderabad": "TELUGU",
    "visakhapatnam": "TELUGU",
    "mumbai": "MARATHI",
    "pune": "MARATHI",
    "thane": "MARATHI",
    "nagpur": "MARATHI",
    "kochi": "MALAYALAM",
    "ahmedabad": "GUJARATI",
    "surat": "GUJARATI",
    "vadodara": "GUJARATI",
    "kolkata": "BENGALI",
}

#: Role keywords to a starting question set. Frontline hiring asks the
#: same handful of things, so a decent default is possible without a model.
_ROLE_PACKS: tuple[tuple[tuple[str, ...], str, list[dict[str, object]]], ...] = (
    (
        ("delivery", "rider", "courier", "logistics", "parcel", "last mile"),
        "Delivery Executive",
        [
            {
                "label": "Years of experience",
                "text": "How many years of delivery experience do you have?",
                "answer_type": "NUMBER",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Owns two-wheeler",
                "text": "Do you have your own two-wheeler and a valid driving licence?",
                "answer_type": "BOOLEAN",
                "weight": 3,
                "is_knockout": True,
            },
            {
                "label": "Smartphone",
                "text": "Do you have a smartphone you can use for work?",
                "answer_type": "BOOLEAN",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Preferred area",
                "text": "Which area of the city would you prefer to work in?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
            {
                "label": "Notice period",
                "text": "How soon are you able to start?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
        ],
    ),
    (
        ("warehouse", "picker", "packer", "dark store", "inventory", "stock"),
        "Warehouse Associate",
        [
            {
                "label": "Warehouse experience",
                "text": "Have you worked in a warehouse or a dark store before?",
                "answer_type": "BOOLEAN",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Preferred shift",
                "text": "Are you able to work night shifts, day shifts, or either?",
                "answer_type": "ENUM",
                "enum_options": ["Night shift", "Day shift", "Either"],
                "weight": 3,
                "is_knockout": False,
            },
            {
                "label": "Can lift 20kg",
                "text": "Are you comfortable lifting boxes of up to twenty kilograms?",
                "answer_type": "BOOLEAN",
                "weight": 3,
                "is_knockout": True,
            },
            {
                "label": "Distance from site",
                "text": "Roughly how many kilometres do you live from the site?",
                "answer_type": "NUMBER",
                "weight": 1,
                "is_knockout": False,
            },
            {
                "label": "Notice period",
                "text": "How soon could you join?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
        ],
    ),
    (
        ("security", "guard", "watchman", "bouncer"),
        "Security Guard",
        [
            {
                "label": "Guard licence",
                "text": "Do you hold a valid security guard licence?",
                "answer_type": "BOOLEAN",
                "weight": 3,
                "is_knockout": True,
            },
            {
                "label": "Years of experience",
                "text": "How many years have you worked in security?",
                "answer_type": "NUMBER",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Preferred shift",
                "text": "Would you prefer day duty or night duty?",
                "answer_type": "ENUM",
                "enum_options": ["Day duty", "Night duty", "Either"],
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Notice period",
                "text": "When can you start?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
        ],
    ),
    (
        (
            "telecall",
            "call centre",
            "call center",
            "bpo",
            "customer support",
            "tele sales",
            "telesales",
        ),
        "Telecaller",
        [
            {
                "label": "Years of experience",
                "text": "How many years of telecalling or customer support experience do you have?",
                "answer_type": "NUMBER",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Languages spoken",
                "text": "Which languages can you speak comfortably with customers?",
                "answer_type": "STRING",
                "weight": 3,
                "is_knockout": False,
            },
            {
                "label": "Comfortable with targets",
                "text": "Are you comfortable working towards daily call targets?",
                "answer_type": "BOOLEAN",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Preferred shift",
                "text": "Which shift would suit you best?",
                "answer_type": "ENUM",
                "enum_options": ["Morning", "Evening", "Night", "Any"],
                "weight": 1,
                "is_knockout": False,
            },
            {
                "label": "Notice period",
                "text": "How soon can you join?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
        ],
    ),
    (
        ("retail", "store", "sales associate", "cashier", "shop floor"),
        "Retail Sales Associate",
        [
            {
                "label": "Retail experience",
                "text": "Have you worked on a shop floor or in retail before?",
                "answer_type": "BOOLEAN",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Years of experience",
                "text": "How many years of retail experience do you have?",
                "answer_type": "NUMBER",
                "weight": 2,
                "is_knockout": False,
            },
            {
                "label": "Weekend availability",
                "text": "Are you available to work on weekends?",
                "answer_type": "BOOLEAN",
                "weight": 3,
                "is_knockout": False,
            },
            {
                "label": "Notice period",
                "text": "How soon are you able to start?",
                "answer_type": "STRING",
                "weight": 1,
                "is_knockout": False,
            },
        ],
    ),
)

#: Used when nothing more specific matches. Deliberately generic but still
#: useful, because an empty form helps nobody.
_GENERIC_PACK: list[dict[str, object]] = [
    {
        "label": "Years of experience",
        "text": "How many years of relevant experience do you have?",
        "answer_type": "NUMBER",
        "weight": 2,
        "is_knockout": False,
    },
    {
        "label": "Interested in role",
        "text": "Does this role sound like something you would like to take up?",
        "answer_type": "BOOLEAN",
        "weight": 2,
        "is_knockout": False,
    },
    {
        "label": "Expected salary",
        "text": "What monthly salary are you expecting?",
        "answer_type": "STRING",
        "weight": 1,
        "is_knockout": False,
    },
    {
        "label": "Notice period",
        "text": "How soon are you able to start?",
        "answer_type": "STRING",
        "weight": 1,
        "is_knockout": False,
    },
]

_TITLE_LINE = re.compile(
    r"(?:^|\n)\s*(?:job\s*title|role|position|designation)\s*[:\-]\s*(.+)", re.IGNORECASE
)
_COMPANY_LINE = re.compile(
    r"(?:^|\n)\s*(?:company|organisation|organization|employer)\s*[:\-]\s*(.+)",
    re.IGNORECASE,
)
#: "join Acme", "hiring for Acme". The company follows the preposition.
_COMPANY_INLINE = re.compile(r"\b(?:at|for|with|join)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")

#: "Acme Logistics is hiring". The most common phrasing by a distance,
#: and the company is the subject rather than the object, so the
#: preposition pattern above never sees it.
_COMPANY_SUBJECT = re.compile(
    r"(?:^|\n|\.\s+)([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})\s+"
    r"(?:is|are)\s+(?:currently\s+)?(?:hiring|looking|seeking|recruiting|expanding)",
    re.MULTILINE,
)


def _find_city(text: str) -> str:
    for city in _CITIES:
        if re.search(rf"\b{re.escape(city)}\b", text, re.IGNORECASE):
            return city
    return ""


def _find_title(text: str) -> str:
    if (match := _TITLE_LINE.search(text)) is not None:
        return match.group(1).strip().strip(".,")[:200]

    # Otherwise the first short line is usually the heading.
    for line in text.splitlines():
        stripped = line.strip(" #*-\t")
        if 3 <= len(stripped) <= 80 and not stripped.endswith("."):
            return stripped[:200]
    return ""


def _looks_like_a_company(candidate: str) -> bool:
    """Reject the things these patterns most often catch by mistake."""
    lowered = candidate.strip().lower()
    if not lowered:
        return False
    if any(city.lower() == lowered for city in _CITIES):
        return False  # "at Bengaluru" is a place, not an employer
    return lowered not in {"we", "our team", "the team", "this role", "the company"}


def _find_company(text: str) -> str:
    if (match := _COMPANY_LINE.search(text)) is not None:
        return match.group(1).strip().strip(".,")[:200]

    for pattern in (_COMPANY_SUBJECT, _COMPANY_INLINE):
        if (match := pattern.search(text)) is not None:
            candidate = match.group(1).strip().strip(".,")
            if _looks_like_a_company(candidate):
                return candidate[:200]
    return ""


def heuristic_draft(jd_text: str) -> JobDraft:
    """Fill the form by keyword matching, with no model involved.

    Runs when there is no API key or the model call fails. Every field is
    a guess the recruiter is expected to correct, which is why this is
    still better than an empty form.
    """
    text = jd_text[:_MAX_JD_CHARS]
    lowered = text.lower()

    matched_title = ""
    questions = _GENERIC_PACK
    for keywords, role_title, pack in _ROLE_PACKS:
        if any(keyword in lowered for keyword in keywords):
            matched_title, questions = role_title, pack
            break

    city = _find_city(text)
    language = _CITY_LANGUAGE.get(city.lower(), "HINDI") if city else "ENGLISH"

    return JobDraft(
        title=_find_title(text) or matched_title or "",
        company_name=_find_company(text) or "",
        location=city,
        language=language,
        voice_persona="NEHA",
        questions=[DraftQuestion.model_validate(question) for question in questions],
        source="heuristic",
        note=(
            "Filled in by keyword matching rather than by reading the description, "
            "so please check every field before creating the role."
        ),
    )


# ── model extraction ─────────────────────────────────────────
async def extract_job_draft(jd_text: str, settings: Settings) -> JobDraft:
    """Read a job description and fill in the whole form.

    Falls back to :func:`heuristic_draft` on any failure. A recruiter who
    pasted a description should always get a filled form back, even when
    the model is unreachable, out of credit, or slow.
    """
    text = jd_text.strip()[:_MAX_JD_CHARS]
    if not text:
        return heuristic_draft("")

    if not settings.openai_api_key:
        logger.info("hiring.jd_extract_no_key", note="using heuristic fallback")
        draft = heuristic_draft(text)
        draft.note = (
            "No language-model key is configured, so these fields were filled in by "
            "keyword matching. Please check them before creating the role."
        )
        return draft

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_LLM_TIMEOUT_SECONDS)
    try:
        # Structured outputs: the response is validated against JobDraft
        # before it reaches us, so there is no prose to parse and no
        # malformed-JSON path to defend against.
        completion = await client.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": f"Prepare a phone screening for this role.\n\n{text}",
                },
            ],
            response_format=JobDraft,
        )
        parsed: JobDraft | None = completion.choices[0].message.parsed
    except (APITimeoutError, APIConnectionError) as exc:
        logger.warning("hiring.jd_extract_unreachable", error=str(exc))
        draft = heuristic_draft(text)
        draft.note = (
            "The language model could not be reached, so these fields were filled in "
            "by keyword matching. Please check them."
        )
        return draft
    except APIStatusError as exc:
        logger.warning(
            "hiring.jd_extract_rejected",
            status=exc.status_code,
            model=settings.llm_model,
        )
        draft = heuristic_draft(text)
        # Name the model. A 404 here almost always means LLM_MODEL is set
        # to something this account cannot reach, and "the request was
        # rejected" alone sends someone hunting in the wrong place.
        reason = (
            f"the model '{settings.llm_model}' is not available on this key"
            if exc.status_code == 404
            else "the language model rejected the request"
        )
        draft.note = (
            f"These fields were filled in by keyword matching because {reason}. Please check them."
        )
        return draft
    finally:
        await client.close()

    if parsed is None:
        # Structured output guarantees a parse when it succeeds, so this
        # is defensive rather than expected. Still better than a 500.
        logger.warning("hiring.jd_extract_empty_parse")
        return heuristic_draft(text)

    parsed.source = "model"
    parsed.note = None
    return _sanitise(parsed)


def _sanitise(draft: JobDraft) -> JobDraft:
    """Constrain a model-produced draft to what the form will accept.

    The form and the API both validate again, but correcting here means a
    slightly out-of-range weight becomes a usable draft rather than a
    validation error the recruiter has to decipher.
    """
    valid_languages = {
        "ENGLISH",
        "HINDI",
        "TAMIL",
        "TELUGU",
        "KANNADA",
        "MARATHI",
        "MALAYALAM",
        "GUJARATI",
        "BENGALI",
    }
    valid_voices = {"NEHA", "ROY", "ZOE", "SAM", "MIRA", "EESHA"}

    draft.language = (
        draft.language.upper() if draft.language.upper() in valid_languages else "ENGLISH"
    )
    draft.voice_persona = (
        draft.voice_persona.upper() if draft.voice_persona.upper() in valid_voices else "NEHA"
    )

    seen: set[str] = set()
    cleaned: list[DraftQuestion] = []
    for question in draft.questions[:15]:
        key = question.label.strip().lower()
        if not key or key in seen or not question.text.strip():
            continue
        seen.add(key)

        question.label = question.label.strip()[:120]
        question.text = question.text.strip()[:500]
        question.weight = min(max(question.weight, 0.0), 10.0)
        if question.answer_type != AnswerType.ENUM.value:
            question.enum_options = []
        if question.answer_type != AnswerType.NUMBER.value:
            question.minimum = None
        # A minimum of zero or less filters nobody out, so it is the same
        # thing as no threshold. Keeping it would leave a requirement that
        # looks enforced and never is.
        if question.minimum is not None and question.minimum <= 0:
            question.minimum = None

        # A knockout needs something to judge against. A boolean one is
        # unambiguous, but a numeric or text requirement with no threshold
        # can never fire, and a "required" badge that does nothing is
        # worse than no badge: it tells a recruiter someone was screened
        # out on a condition that was never actually applied.
        cannot_judge = (
            question.answer_type == AnswerType.NUMBER.value and question.minimum is None
        ) or question.answer_type in (AnswerType.STRING.value, AnswerType.ENUM.value)
        if question.is_knockout and cannot_judge:
            question.is_knockout = False

        cleaned.append(question)

    draft.questions = cleaned or heuristic_draft("").questions
    return draft


def to_question_inputs(draft: JobDraft) -> list[QuestionInput]:
    """Convert a draft into the API's question shape, for tests and reuse."""
    return [
        QuestionInput(
            label=question.label,
            text=question.text,
            answer_type=AnswerType(question.answer_type),
            enum_options=question.enum_options or None,
            extraction_hint=None,
            weight=question.weight,
            is_knockout=question.is_knockout,
            scoring_rule=None,
        )
        for question in draft.questions
    ]
