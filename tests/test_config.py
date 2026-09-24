from config import Settings
from pydantic import ValidationError
import pytest


def test_supabase_postgres_url_is_normalized_for_psycopg():
    settings = Settings(
        database_url=(
            "postgresql://postgres.project:pass@aws-0-eu.pooler.supabase.com:6543/"
            "postgres?sslmode=require&channel_binding=require"
        )
    )
    assert settings.database_url == (
        "postgresql+psycopg://postgres.project:pass@aws-0-eu.pooler.supabase.com:6543/"
        "postgres?sslmode=require"
    )


def test_supabase_url_enables_ssl_for_psycopg():
    settings = Settings(
        database_url=(
            "postgresql://postgres.project:pass@aws-0-eu.pooler.supabase.com:6543/postgres"
        )
    )
    assert settings.database_url.startswith(
        "postgresql+psycopg://postgres.project:pass@aws-0-eu.pooler.supabase.com:6543/postgres?"
    )
    assert "sslmode=require" in settings.database_url


def test_positive_group_id_is_rejected():
    with pytest.raises(ValidationError):
        Settings(telegram_group_id=123)


def test_empty_admin_id_from_env_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "")
    assert Settings().admin_telegram_id == 0


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///local.db",
        "postgresql://user:pass@example.com:6543/postgres",
        "postgresql://postgres.project:pass@aws-0-eu.pooler.supabase.com:5432/postgres",
    ],
)
def test_non_transaction_pooler_database_url_is_rejected(database_url):
    with pytest.raises(ValidationError):
        Settings(database_url=database_url)
