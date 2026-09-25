from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from asyncio_compat import run
from bot.commands import register_private_commands
from runtime import get_runtime

MSK = ZoneInfo("Europe/Moscow")
logger = logging.getLogger(__name__)


async def scheduler_loop() -> None:
    runtime = await get_runtime()
    last_tick: tuple[int, int, int, int] | None = None
    while True:
        now = datetime.now(MSK)
        tick = (now.year, now.timetuple().tm_yday, now.hour, now.minute)
        if now.minute == 2 and tick != last_tick:
            last_tick = tick
            try:
                await runtime.jobs.run_scheduled()
            except Exception:
                logger.exception("Local scheduler iteration failed")
        await asyncio.sleep(10)


async def main() -> None:
    runtime = await get_runtime()
    scheduler = asyncio.create_task(scheduler_loop())
    try:
        await runtime.bot.delete_webhook(drop_pending_updates=False)
        await register_private_commands(runtime.bot)
        await runtime.dispatcher.start_polling(
            runtime.bot, allowed_updates=["message", "callback_query"]
        )
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await runtime.close()


if __name__ == "__main__":
    run(main())
