from fastapi import FastAPI

from api.health import router as health_router
from api.hourly_cron import router as cron_router
from api.telegram_webhook import router as webhook_router

app = FastAPI(title="Money-X Checker", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(health_router)
app.include_router(webhook_router)
app.include_router(cron_router)
