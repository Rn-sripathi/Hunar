"""Turn a job description into people-search filters.

A job description is prose. A people-search API wants titles, seniority,
skills and a city. Something has to translate, and the translation is a
guess however it is made.

So it is never applied silently. The extracted filters are returned to
the recruiter, shown as editable chips, and only searched once they say
go. That makes the model a suggestion engine rather than an oracle in
the critical path, and it means a bad search can be traced to the filters
that produced it rather than blamed on the provider.

Three tiers, and the last one matters most:

1. Model extraction, which reads the description properly.
2. Keyword extraction, when there is no key or the call fails.
3. An empty, editable filter set with the description still loaded.

The third is the one that keeps the screen usable when everything else
has gone wrong. The app never dead-ends on a failed extraction.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.people.schemas import SearchFilters

logger = structlog.get_logger(__name__)

__all__ = ["ExtractionResult", "extract_filters", "heuristic_filters"]

_MAX_JD_CHARS = 12_000
_TIMEOUT_SECONDS = 60.0


class ExtractionResult(BaseModel):
    """Filters, plus an honest account of where they came from."""

    filters: SearchFilters
    method: Literal["llm", "heuristic", "manual"]
    note: str | None = None


_SYSTEM = """\
You turn a job description into search filters for a professional people \
database, so a recruiter can find people who are not applicants.

Think about who would actually be a good approach for this role, not just \
which words appear in the description.

- titles: the job titles these people hold TODAY, which is often not the \
title being hired for. Someone right for a Senior Backend Engineer opening \
may currently be a Backend Engineer, a Software Engineer or a Member of \
Technical Staff. Give three to six realistic variants.
- excluded_titles: titles that superficially match but are wrong. For a \
backend role that might be Frontend Engineer or QA Engineer.
- seniorities: from ic, senior, lead, manager, director, vp, cxo. Usually \
one or two adjacent levels.
- skills_required: only what the description states as necessary.
- skills_nice: the rest.
- cities: the city or cities. Use the current Indian name, so Bengaluru \
rather than Bangalore.
- industries: only when the description implies the person should come from \
one, such as fintech or healthcare.
- min_years and max_years: only when the description states a range. Null \
otherwise. Never invent a floor.
- exclude_companies: only if the description names companies to avoid.
- role_pitch: ONE sentence, under 180 characters, that a recruiter would say \
out loud on the phone to make this role sound worth two minutes. Concrete, \
not corporate. "a Series B fintech in Bengaluru hiring a senior backend \
engineer to own their payments ledger" rather than "an exciting opportunity \
at a fast-growing company".
- comp_range_text: the compensation as a person would say it aloud, such as \
"35 to 50 lakhs per annum". Empty string if the description gives none.
- work_mode: onsite, hybrid or remote if stated, else empty.

Be specific rather than broad. A search returning fifty loosely relevant \
people is worse than one returning eight good ones, because someone has to \
read them all.
"""


# ── heuristic tier ───────────────────────────────────────────
_CITIES = (
    "Bengaluru",
    "Bangalore",
    "Mumbai",
    "Delhi",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Ahmedabad",
    "Gurugram",
    "Gurgaon",
    "Noida",
    "Jaipur",
    "Kochi",
    "Coimbatore",
    "Chandigarh",
    "Indore",
)

_CITY_CANONICAL = {"bangalore": "Bengaluru", "gurgaon": "Gurugram", "bombay": "Mumbai"}

_SKILLS = (
    "python",
    "typescript",
    "javascript",
    "java",
    "kotlin",
    "golang",
    "go",
    "rust",
    "c++",
    "ruby",
    "php",
    "scala",
    "react",
    "next.js",
    "node",
    "django",
    "fastapi",
    "flask",
    "spring",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "kafka",
    "spark",
    "airflow",
    "dbt",
    "aws",
    "gcp",
    "azure",
    "kubernetes",
    "docker",
    "terraform",
    "machine learning",
    "pytorch",
    "tensorflow",
    "llm apis",
    "sql",
    "excel",
    "salesforce",
    "sap",
)

_SENIORITY_WORDS: tuple[tuple[str, str], ...] = (
    ("chief", "cxo"),
    ("cto", "cxo"),
    ("ceo", "cxo"),
    ("vp ", "vp"),
    ("vice president", "vp"),
    ("director", "director"),
    ("head of", "manager"),
    ("manager", "manager"),
    ("principal", "lead"),
    ("staff", "lead"),
    ("lead", "lead"),
    ("senior", "senior"),
    ("sr.", "senior"),
    ("sr ", "senior"),
    ("junior", "ic"),
    ("associate", "ic"),
    ("intern", "ic"),
)

_TITLE_LINE = re.compile(
    r"(?:^|\n)\s*(?:job\s*title|role|position|designation)\s*[:\-]\s*(.+)", re.IGNORECASE
)
_YEARS = re.compile(
    # The en dash is deliberate. Pasted job descriptions use it for ranges
    # far more often than a plain hyphen, and missing it loses the match.
    r"(?:minimum|min\.?|at least|)\s*(\d{1,2})\s*(?:\+|to|-|–)?\s*(\d{1,2})?\s*years?",  # noqa: RUF001
    re.IGNORECASE,
)
_LPA = re.compile(
    r"(\d{1,3})\s*(?:to|-|–)\s*(\d{1,3})\s*(?:lpa|lakhs?)",  # noqa: RUF001
    re.IGNORECASE,
)


def heuristic_filters(jd_text: str) -> SearchFilters:
    """Derive filters by keyword matching, with no model involved."""
    text = jd_text[:_MAX_JD_CHARS]
    lowered = text.lower()

    title = ""
    if (match := _TITLE_LINE.search(text)) is not None:
        title = match.group(1).strip().strip(".,")[:120]
    else:
        for line in text.splitlines():
            stripped = line.strip(" #*-\t")
            if 3 <= len(stripped) <= 80 and not stripped.endswith("."):
                title = stripped.split(" - ")[0].split(" – ")[0].strip()[:120]  # noqa: RUF001
                break

    cities: list[str] = []
    for city in _CITIES:
        if re.search(rf"\b{re.escape(city)}\b", text, re.IGNORECASE):
            cities.append(_CITY_CANONICAL.get(city.lower(), city))

    seniorities: list[str] = []
    for word, level in _SENIORITY_WORDS:
        if word in lowered and level not in seniorities:
            seniorities.append(level)

    skills = [skill for skill in _SKILLS if re.search(rf"\b{re.escape(skill)}\b", lowered)]

    min_years: int | None = None
    max_years: int | None = None
    if (match := _YEARS.search(text)) is not None:
        min_years = int(match.group(1))
        if match.group(2):
            max_years = int(match.group(2))

    comp = ""
    if (match := _LPA.search(text)) is not None:
        comp = f"{match.group(1)} to {match.group(2)} lakhs per annum"

    work_mode = ""
    for mode in ("remote", "hybrid", "onsite", "on-site"):
        if mode in lowered:
            work_mode = "onsite" if mode == "on-site" else mode
            break

    pitch = f"a {title} role" if title else "a role"
    if cities:
        pitch += f" in {cities[0]}"

    return SearchFilters(
        hiring_title=title,
        titles=[title] if title else [],
        seniorities=list(seniorities[:2]),  # type: ignore[arg-type]
        skills_required=skills[:6],
        skills_nice=skills[6:12],
        cities=cities[:3],
        min_years=min_years,
        max_years=max_years,
        role_pitch=pitch[:240],
        comp_range_text=comp,
        work_mode=work_mode,
    )


# ── model tier ───────────────────────────────────────────────
class _Extracted(BaseModel):
    """The model's answer, kept separate from the API-facing shape.

    Structured outputs reject optional-with-default in some positions, so
    this mirrors SearchFilters with everything required and is mapped
    across afterwards.
    """

    hiring_title: str = Field(description="The role being advertised")
    company_name: str
    titles: list[str] = Field(description="Titles these people hold today")
    excluded_titles: list[str]
    seniorities: list[str]
    skills_required: list[str]
    skills_nice: list[str]
    cities: list[str]
    industries: list[str]
    exclude_companies: list[str]
    min_years: int | None
    max_years: int | None
    role_pitch: str
    comp_range_text: str
    work_mode: str


_VALID_SENIORITIES = {"ic", "senior", "lead", "manager", "director", "vp", "cxo"}


async def extract_filters(jd_text: str, settings: Settings) -> ExtractionResult:
    """Read a job description and propose search filters.

    Falls back to keyword extraction on any failure, and always reports
    which tier produced the result so the UI can say how much to trust it.
    """
    text = jd_text.strip()[:_MAX_JD_CHARS]
    if not text:
        return ExtractionResult(filters=SearchFilters(), method="heuristic")

    if not settings.openai_api_key:
        return ExtractionResult(
            filters=heuristic_filters(text),
            method="heuristic",
            note=(
                "No language-model key is configured, so these filters came from "
                "keyword matching. Check them before searching."
            ),
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_TIMEOUT_SECONDS)
    try:
        completion = await client.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Find people for this role.\n\n{text}"},
            ],
            response_format=_Extracted,
        )
        parsed = completion.choices[0].message.parsed
    except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
        logger.warning("people.filter_extraction_failed", error=type(exc).__name__)
        return ExtractionResult(
            filters=heuristic_filters(text),
            method="heuristic",
            note=(
                "The language model was unavailable, so these filters came from "
                "keyword matching. Check them before searching."
            ),
        )
    finally:
        await client.close()

    if parsed is None:
        return ExtractionResult(filters=heuristic_filters(text), method="heuristic")

    return ExtractionResult(
        filters=SearchFilters(
            hiring_title=parsed.hiring_title[:200],
            company_name=parsed.company_name[:200],
            titles=parsed.titles,
            excluded_titles=parsed.excluded_titles,
            seniorities=[s for s in parsed.seniorities if s in _VALID_SENIORITIES],  # type: ignore[misc]
            skills_required=parsed.skills_required,
            skills_nice=parsed.skills_nice,
            cities=parsed.cities,
            industries=parsed.industries,
            exclude_companies=parsed.exclude_companies,
            min_years=parsed.min_years,
            max_years=parsed.max_years,
            role_pitch=parsed.role_pitch[:240],
            comp_range_text=parsed.comp_range_text,
            work_mode=parsed.work_mode,
        ),
        method="llm",
    )
