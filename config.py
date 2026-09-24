from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MONEYX_WEB_URL = "https://mxc1n.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    telegram_bot_token: str = ""
    admin_telegram_id: int = 0
    telegram_group_id: int = 0
    telegram_webhook_secret: str = ""
    cron_secret: str = ""
    app_encryption_key: str = ""
    database_url: str = ""
    app_base_url: str = ""

    moneyx_timeout_seconds: float = Field(default=12.0, ge=1, le=30)
    moneyx_total_deadline_seconds: float = Field(default=45.0, ge=5, le=240)
    check_cooldown_seconds: int = Field(default=30, ge=0, le=3600)

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgres://")
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
        elif value.startswith("postgresql+asyncpg://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql+asyncpg://")
        if value.startswith("postgresql+psycopg://"):
            parsed = urlsplit(value)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if "ssl" in query and "sslmode" not in query:
                query["sslmode"] = query.pop("ssl")
            query.pop("channel_binding", None)
            query.pop("prepared_statement_cache_size", None)
            if parsed.hostname and parsed.hostname.endswith((".supabase.com", ".supabase.co")):
                query.setdefault("sslmode", "require")
            value = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
            )
        return value

    @field_validator("database_url")
    @classmethod
    def validate_supabase_pooler_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if parsed.scheme != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must be a PostgreSQL URL from Supabase")
        if not hostname.endswith((".supabase.com", ".supabase.co")):
            raise ValueError("DATABASE_URL must point to a Supabase host")
        if parsed.port != 6543:
            raise ValueError("DATABASE_URL must use the Supabase Transaction pooler on port 6543")
        return value

    @field_validator("admin_telegram_id", "telegram_group_id", mode="before")
    @classmethod
    def empty_telegram_id_is_unconfigured(cls, value: object) -> object:
        return 0 if value in (None, "") else value

    @field_validator("telegram_group_id")
    @classmethod
    def validate_group_id(cls, value: int) -> int:
        if value > 0:
            raise ValueError("TELEGRAM_GROUP_ID must be a negative group or supergroup ID")
        if value < -(2**63):
            raise ValueError("TELEGRAM_GROUP_ID is outside signed BIGINT range")
        return value

    def validate_runtime(self) -> list[str]:
        missing: list[str] = []
        for name in (
            "telegram_bot_token",
            "admin_telegram_id",
            "telegram_group_id",
            "telegram_webhook_secret",
            "cron_secret",
            "app_encryption_key",
            "database_url",
        ):
            if not getattr(self, name):
                missing.append(name.upper())
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
