"""HTTP surface for the sourcing domain."""

from __future__ import annotations

from fastapi import APIRouter

from app.people.routers.campaigns import router as campaigns_router
from app.people.routers.search import router as search_router

router = APIRouter()
router.include_router(search_router)
router.include_router(campaigns_router)

__all__ = ["router"]
