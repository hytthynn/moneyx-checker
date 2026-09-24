import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import NullPool

from security import SecretBox
from storage.repository import Repository, build_rate_snapshot_insert, token_fingerprint


def secret_box() -> SecretBox:
    return SecretBox(Fernet.generate_key().decode())


def test_token_fingerprint_is_stable_and_optional():
    assert token_fingerprint(None) is None
    assert token_fingerprint("token") == token_fingerprint("token")
    assert len(token_fingerprint("token") or "") == 64


def test_repository_rejects_sqlite():
    with pytest.raises(ValueError, match="Supabase PostgreSQL"):
        Repository("sqlite+aiosqlite:///test.db", secret_box())


def test_rate_snapshots_use_one_multirow_insert():
    values = [
        {
            "delivery_run_id": 1,
            "captured_at": None,
            "web_url": "https://mxc1o.com",
            "currency": currency,
            "network": "TEST",
            "rate": None,
            "status": "error",
            "error": "test",
        }
        for currency in ("USDT", "BTC", "TON")
    ]

    sql = str(build_rate_snapshot_insert(values).compile(dialect=postgresql.dialect()))

    assert sql.count("), (") == 2


@pytest.mark.asyncio
async def test_supabase_repository_is_serverless_safe():
    repository = Repository(
        "postgresql+psycopg://postgres.project:password@"
        "aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require",
        secret_box(),
    )
    try:
        assert isinstance(repository.engine.sync_engine.pool, NullPool)
        assert repository.engine.sync_engine.dialect.default_schema_name == "public"
        assert repository.engine.sync_engine.dialect.server_version_info == (17, 0)
        assert repository.engine.sync_engine.dialect.skip_autocommit_rollback is True
    finally:
        await repository.close()
