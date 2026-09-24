from __future__ import annotations

import getpass

from asyncio_compat import run
from config import get_settings
from moneyx.client import MoneyXClient
from security import SecretBox
from storage.repository import Repository


async def main() -> None:
    config = get_settings()
    repository = Repository(config.database_url, SecretBox(config.app_encryption_key))
    token = getpass.getpass("Money-X token (hidden): ").strip()
    mxi = getpass.getpass("mxi_token or Enter (hidden): ").strip() or None
    try:
        state = await repository.get_settings()
        async with MoneyXClient(
            state.web_url,
            state.api_url,
            token=token,
            mxi_token=mxi,
            timeout=config.moneyx_timeout_seconds,
        ) as client:
            await client.authenticate()
            await client.list_crypto()
        await repository.save_secrets(token, mxi)
    finally:
        await repository.close()
    print("Authentication verified and encrypted in the database.")


if __name__ == "__main__":
    run(main())
