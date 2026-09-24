from __future__ import annotations

from fastapi import APIRouter

from config import get_settings
from runtime import get_runtime

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, object]:
    config = get_settings()
    missing = config.validate_runtime()
    if missing:
        return {"status": "misconfigured", "missing": missing, "database": "not_checked"}
    try:
        runtime = await get_runtime()
        await runtime.repository.ping()
    except Exception:
        return {"status": "degraded", "missing": [], "database": "unavailable"}
    return {"status": "ok", "missing": [], "database": "ok"}
