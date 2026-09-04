"""Candidate routes, including spreadsheet import.

The import is written to be forgiving about formatting and unforgiving
about ambiguity. Recruiters export from wildly different systems, so
column names are matched loosely and phone numbers are normalised rather
than rejected for containing spaces. But a row that cannot be turned into
a dialable number is reported back with its reason rather than dropped,
because someone who uploads two hundred rows and gets a hundred and
eighty candidates needs to know which twenty were lost and why.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

import phonenumbers
import structlog
from fastapi import APIRouter, File, UploadFile, status
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.deps import DbSession
from app.hiring.models import Candidate, CandidateSource
from app.hiring.schemas import (
    CandidateCreate,
    CandidateImportReport,
    CandidateOut,
    DecisionUpdate,
)
from app.hiring.services import job_service
from app.hiring.services.call_service import mask_number

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs/{job_id}/candidates")

#: Header spellings seen in real recruiter exports.
_NAME_HEADERS = ("name", "candidate", "candidate name", "full name", "applicant", "fullname")
_PHONE_HEADERS = (
    "mobile",
    "phone",
    "mobile number",
    "phone number",
    "contact",
    "contact number",
    "mobile_number",
    "phone_number",
    "number",
    "cell",
    "whatsapp",
)

_MAX_IMPORT_BYTES = 2 * 1024 * 1024
_MAX_IMPORT_ROWS = 2000


def _to_out(candidate: Candidate) -> CandidateOut:
    out = CandidateOut.model_validate(candidate)
    out.mobile_masked = mask_number(candidate.mobile_number)
    return out


def _normalise_indian_mobile(raw: str) -> str | None:
    """Parse a number the way a recruiter is likely to have typed it.

    Defaults to India, since that is who this product screens, but accepts
    an explicit country code for anything else.
    """
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        parsed = phonenumbers.parse(candidate, None if candidate.startswith("+") else "IN")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


@router.get("", response_model=list[CandidateOut])
async def list_candidates(job_id: uuid.UUID, session: DbSession) -> list[CandidateOut]:
    await job_service.get_job(session, job_id)
    result = await session.execute(
        select(Candidate).where(Candidate.job_id == job_id).order_by(Candidate.created_at)
    )
    return [_to_out(candidate) for candidate in result.scalars()]


@router.post("", response_model=CandidateOut, status_code=status.HTTP_201_CREATED)
async def add_candidate(
    job_id: uuid.UUID, payload: CandidateCreate, session: DbSession
) -> CandidateOut:
    await job_service.get_job(session, job_id)

    existing = await session.scalar(
        select(Candidate.id).where(
            Candidate.job_id == job_id, Candidate.mobile_number == payload.mobile_number
        )
    )
    if existing is not None:
        raise ConflictError(
            "This number is already a candidate for this role. Adding it again "
            "would mean calling the same person twice about the same job."
        )

    candidate = Candidate(
        job_id=job_id,
        name=payload.name,
        mobile_number=payload.mobile_number,
        raw_mobile=payload.mobile_number,
        extra=payload.extra,
        source=CandidateSource.MANUAL.value,
    )
    session.add(candidate)
    await session.flush()
    return _to_out(candidate)


@router.post("/import", response_model=CandidateImportReport)
async def import_candidates(
    job_id: uuid.UUID, session: DbSession, file: UploadFile = File(...)
) -> CandidateImportReport:
    """Import candidates from a CSV export."""
    await job_service.get_job(session, job_id)

    raw = await file.read()
    if len(raw) > _MAX_IMPORT_BYTES:
        raise ValidationFailedError("That file is larger than the 2 MB import limit.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows commonly writes cp1252 rather than UTF-8.
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            raise ValidationFailedError(
                "That file is not readable as text. Export it as CSV and retry."
            ) from None

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationFailedError("The file has no header row.")

    lookup = {(name or "").strip().lower(): name for name in reader.fieldnames}
    name_column = next((lookup[key] for key in _NAME_HEADERS if key in lookup), None)
    phone_column = next((lookup[key] for key in _PHONE_HEADERS if key in lookup), None)

    if phone_column is None:
        raise ValidationFailedError(
            "No phone number column found. Name one of the columns "
            f"'{_PHONE_HEADERS[0]}' or 'phone'. Columns found: "
            f"{', '.join(reader.fieldnames)}"
        )

    existing_numbers = set(
        (await session.execute(select(Candidate.mobile_number).where(Candidate.job_id == job_id)))
        .scalars()
        .all()
    )

    imported: list[Candidate] = []
    rejected: list[dict[str, str]] = []
    duplicates = 0
    seen_in_file: set[str] = set()

    for line_number, row in enumerate(reader, start=2):
        if len(imported) >= _MAX_IMPORT_ROWS:
            rejected.append(
                {"row": str(line_number), "reason": f"import capped at {_MAX_IMPORT_ROWS} rows"}
            )
            break

        raw_phone = (row.get(phone_column) or "").strip()
        name = (row.get(name_column) or "").strip() if name_column else ""

        if not raw_phone and not name:
            continue  # a blank trailing line, not an error

        normalised = _normalise_indian_mobile(raw_phone)
        if normalised is None:
            rejected.append(
                {
                    "row": str(line_number),
                    "name": name or "(no name)",
                    "value": raw_phone or "(empty)",
                    "reason": "not a valid phone number",
                }
            )
            continue

        if normalised in existing_numbers or normalised in seen_in_file:
            duplicates += 1
            continue

        seen_in_file.add(normalised)
        # Unmapped columns ride along as custom data, so a prompt can
        # reference them without this importer needing to know about them.
        extra = {
            key: str(value).strip()
            for key, value in row.items()
            if key not in {name_column, phone_column} and value and str(value).strip()
        }

        candidate = Candidate(
            job_id=job_id,
            name=name or f"Candidate {normalised[-4:]}",
            mobile_number=normalised,
            raw_mobile=raw_phone,
            extra=extra,
            source=CandidateSource.CSV.value,
        )
        session.add(candidate)
        imported.append(candidate)

    await session.flush()
    logger.info(
        "hiring.candidates_imported",
        job_id=str(job_id),
        imported=len(imported),
        duplicates=duplicates,
        rejected=len(rejected),
    )

    return CandidateImportReport(
        imported=len(imported),
        skipped_duplicates=duplicates,
        rejected=rejected,
        candidates=[_to_out(candidate) for candidate in imported],
    )


@router.patch("/{candidate_id}/decision", response_model=CandidateOut)
async def set_decision(
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    payload: DecisionUpdate,
    session: DbSession,
) -> CandidateOut:
    """Record a recruiter's shortlist or reject decision.

    The score ranks; a person decides. This endpoint is what keeps the
    automated ranking advisory rather than determinative.
    """
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise NotFoundError(f"No candidate {candidate_id} on this role")

    candidate.decision = payload.decision.value
    candidate.decision_note = payload.note
    candidate.decided_at = datetime.now(UTC)
    await session.flush()
    return _to_out(candidate)


@router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(job_id: uuid.UUID, candidate_id: uuid.UUID, session: DbSession) -> None:
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None or candidate.job_id != job_id:
        raise NotFoundError(f"No candidate {candidate_id} on this role")
    await session.delete(candidate)


__all__ = ["router"]
