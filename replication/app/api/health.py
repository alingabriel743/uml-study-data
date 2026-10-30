"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(prefix="/ai")


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "diagrams-v2", "version": "2.0.0"}
