"""HTTP routes for the hiring domain.

Routers stay thin: they validate, delegate to a service, and shape a
response. Anything resembling a decision belongs in ``services``, where
it can be tested without an HTTP request.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.hiring.routers import calls, candidates, jobs

router = APIRouter(prefix="/hiring", tags=["hiring"])
router.include_router(jobs.router)
router.include_router(candidates.router)
router.include_router(calls.router)

__all__ = ["router"]
