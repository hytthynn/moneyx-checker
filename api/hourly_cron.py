from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from runtime import get_runtime
from security import secure_equals

router = APIRouter()


def _bearer(value: str | None) -> str | None:
    if not value or not value.startswith("Bearer "):
        return None
    return value.removeprefix("Bearer ")


@router.get("/api/hourly/02")
@router.post("/api/hourly/02")
async def hourly(authorization: str | None = Header(default=None)) -> dict[str, object]:
    runtime = await get_runtime()
    supplied = _bearer(authorization)
    if not secure_equals(supplied, runtime.config.cron_secret):
        raise HTTPException(status_code=401, detail="unauthorized")
    result = await runtime.jobs.run_scheduled()
    return {"status": result.status, "detail": result.detail, "sent_parts": result.sent_parts}
