from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from moneyx.models import RateBatch
from security import SecretBox, redact
from storage.models import (
    AdminAlert,
    AppSettings,
    DeliveryRun,
    PendingAction,
    RateSnapshot,
    StoredSecrets,
)


@dataclass(slots=True, frozen=True)
class SecretValues:
    token: str | None
    mxi_token: str | None
    auth_valid: bool
    updated_at: datetime | None
    token_fingerprint: str | None = None


@dataclass(slots=True, frozen=True)
class PendingValue:
    action: str
    payload: str | None


def token_fingerprint(token: str | None) -> str | None:
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def build_rate_snapshot_insert(values: list[dict[str, object]]):
    """Build one multi-row INSERT; Supavisor transaction mode rejects executemany."""
    return insert(RateSnapshot).values(values)


class Repository:
    def __init__(self, database_url: str, secret_box: SecretBox):
        parsed = urlsplit(database_url)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "postgresql+psycopg"
            or not hostname.endswith((".supabase.com", ".supabase.co"))
            or parsed.port != 6543
        ):
            raise ValueError("DATABASE_URL must be a Supabase PostgreSQL pooler URL")
        kwargs: dict[str, object] = {
            "poolclass": NullPool,
            "connect_args": {"prepare_threshold": None},
            "isolation_level": "AUTOCOMMIT",
            "skip_autocommit_rollback": True,
        }
        self.engine: AsyncEngine = create_async_engine(database_url, **kwargs)
        self._configure_supabase_dialect()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.secret_box = secret_box

    def _configure_supabase_dialect(self) -> None:
        """Avoid four dialect-probe queries that some Supavisor links terminate."""
        dialect = self.engine.sync_engine.dialect
        dialect.server_version_info = (17, 0)
        dialect.default_schema_name = "public"
        dialect.default_isolation_level = "READ COMMITTED"
        dialect.max_identifier_length = 63
        dialect.supports_smallserial = True
        dialect._backslash_escapes = False
        dialect._supports_drop_index_concurrently = True
        dialect.supports_identity_columns = True
        dialect._supports_jsonb_subscripting = True
        dialect.initialize = lambda connection: None

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))

    @asynccontextmanager
    async def job_lock(self):
        """Cross-instance lease implemented as two pooler-safe single statements."""
        key = "__moneyx_job_lock__"
        claimed_at = datetime.now(UTC)
        expired_before = claimed_at - timedelta(minutes=2)
        async with self.sessions() as session:
            acquired = bool(
                await session.scalar(
                    text(
                        """
                        INSERT INTO admin_alerts (key, sent_at)
                        VALUES (:key, :claimed_at)
                        ON CONFLICT (key) DO UPDATE
                        SET sent_at = EXCLUDED.sent_at
                        WHERE admin_alerts.sent_at < :expired_before
                        RETURNING key
                        """
                    ),
                    {
                        "key": key,
                        "claimed_at": claimed_at,
                        "expired_before": expired_before,
                    },
                )
            )
        try:
            yield acquired
        finally:
            if acquired:
                async with self.sessions() as session:
                    await session.execute(
                        delete(AdminAlert).where(
                            AdminAlert.key == key,
                            AdminAlert.sent_at == claimed_at,
                        )
                    )

    async def get_settings(self) -> AppSettings:
        async with self.sessions() as session:
            result = await session.get(AppSettings, 1)
            if result is None:
                raise RuntimeError(
                    "Database schema is not initialized; execute "
                    "migrations/001_initial.sql in the Supabase SQL Editor"
                )
            session.expunge(result)
            return result

    async def set_domain(self, web_url: str, api_url: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(AppSettings)
                .where(AppSettings.id == 1)
                .values(web_url=web_url, api_url=api_url, updated_at=datetime.now(UTC))
            )

    async def mark_check(self) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(AppSettings)
                .where(AppSettings.id == 1)
                .values(last_check_at=datetime.now(UTC))
            )

    async def save_secrets(self, token: str, mxi_token: str | None, *, valid: bool = True) -> None:
        async with self.sessions.begin() as session:
            values = {
                "token_encrypted": self.secret_box.encrypt(token),
                "mxi_token_encrypted": self.secret_box.encrypt(mxi_token),
                "token_fingerprint": token_fingerprint(token),
                "auth_valid": valid,
                "updated_at": datetime.now(UTC),
            }
            result = await session.execute(
                update(StoredSecrets).where(StoredSecrets.id == 1).values(**values)
            )
            if result.rowcount == 0:
                session.add(StoredSecrets(id=1, **values))
            await session.execute(delete(AdminAlert).where(AdminAlert.key == "auth_expired"))

    async def get_secrets(self) -> SecretValues:
        async with self.sessions() as session:
            row = await session.get(StoredSecrets, 1)
            if row is None:
                return SecretValues(None, None, False, None)
            return SecretValues(
                self.secret_box.decrypt(row.token_encrypted),
                self.secret_box.decrypt(row.mxi_token_encrypted),
                row.auth_valid,
                row.updated_at,
                row.token_fingerprint,
            )

    async def clear_secrets(self) -> None:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(StoredSecrets)
                .where(StoredSecrets.id == 1)
                .values(
                    token_encrypted=None,
                    mxi_token_encrypted=None,
                    token_fingerprint=None,
                    auth_valid=False,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount == 0:
                session.add(StoredSecrets(id=1))

    async def mark_auth_invalid(self, token: str | None = None) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(StoredSecrets)
                .where(StoredSecrets.id == 1)
                .values(auth_valid=False, token_fingerprint=token_fingerprint(token))
            )

    async def mark_auth_valid(self, token: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(StoredSecrets)
                .where(StoredSecrets.id == 1)
                .values(auth_valid=True, token_fingerprint=token_fingerprint(token))
            )

    async def put_pending(
        self, admin_id: int, action: str, payload: str | None = None, ttl_minutes: int = 10
    ) -> None:
        encrypted = self.secret_box.encrypt(payload)
        expires = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        async with self.sessions.begin() as session:
            await session.execute(delete(PendingAction).where(PendingAction.admin_id == admin_id))
            session.add(
                PendingAction(
                    admin_id=admin_id,
                    action=action,
                    payload_encrypted=encrypted,
                    expires_at=expires,
                )
            )

    async def pop_pending(self, admin_id: int) -> PendingValue | None:
        async with self.sessions.begin() as session:
            row = await session.get(PendingAction, admin_id)
            if row is None:
                return None
            await session.delete(row)
            if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) < datetime.now(UTC):
                return None
            return PendingValue(row.action, self.secret_box.decrypt(row.payload_encrypted))

    async def claim_delivery(self, scheduled_hour: str, chat_id: int) -> int | None:
        async with self.sessions() as session:
            try:
                async with session.begin():
                    row = DeliveryRun(scheduled_hour=scheduled_hour, chat_id=chat_id)
                    session.add(row)
                    await session.flush()
                    run_id = row.id
                return run_id
            except IntegrityError:
                await session.rollback()
                return None

    async def finish_delivery(self, run_id: int, status: str, error: str | None = None) -> None:
        safe_error = redact(error)[:1000] if error else None
        async with self.sessions.begin() as session:
            await session.execute(
                update(DeliveryRun)
                .where(DeliveryRun.id == run_id)
                .values(status=status, error=safe_error, finished_at=datetime.now(UTC))
            )

    async def save_rates(self, run_id: int, web_url: str, batch: RateBatch) -> None:
        values: list[dict[str, object]] = []
        now = datetime.now(UTC)
        values.extend(
            {
                "delivery_run_id": run_id,
                "captured_at": now,
                "web_url": web_url,
                "currency": rate.currency,
                "network": rate.network,
                "rate": rate.rate,
                "status": "ok",
                "error": None,
            }
            for rate in batch.rates
        )
        values.extend(
            {
                "delivery_run_id": run_id,
                "captured_at": now,
                "web_url": web_url,
                "currency": failure.currency,
                "network": failure.network,
                "rate": None,
                "status": "error",
                "error": redact(failure.error)[:500],
            }
            for failure in batch.failures
        )
        if values:
            async with self.sessions.begin() as session:
                await session.execute(build_rate_snapshot_insert(values))

    async def claim_alert(self, key: str) -> bool:
        async with self.sessions() as session:
            try:
                async with session.begin():
                    session.add(AdminAlert(key=key))
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def reset_alert(self, key: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(AdminAlert).where(AdminAlert.key == key))
