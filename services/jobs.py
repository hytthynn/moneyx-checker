from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import Settings
from moneyx.client import AuthenticationError, ForbiddenError, MoneyXClient
from moneyx.rates import collect_rates
from security import redact
from services.notifier import format_report, send_report
from storage.repository import Repository, SecretValues

logger = logging.getLogger(__name__)
DELIVERY_KEY_MAX_LENGTH = 40


def new_delivery_key(kind: str) -> str:
    key = f"{kind}:{uuid.uuid4().hex}"
    assert len(key) <= DELIVERY_KEY_MAX_LENGTH
    return key


@dataclass(frozen=True, slots=True)
class JobResult:
    status: str
    detail: str
    sent_parts: int = 0


class JobService:
    def __init__(self, repository: Repository, bot: Bot, config: Settings):
        self.repository = repository
        self.bot = bot
        self.config = config

    async def _secrets(self) -> SecretValues:
        return await self.repository.get_secrets()

    async def run_scheduled(self) -> JobResult:
        async with self.repository.job_lock() as acquired:
            if not acquired:
                return JobResult("busy", "another rate check is already running")
            key = new_delivery_key("cron")
            run_id = await self.repository.claim_delivery(key, self.config.telegram_group_id)
            assert run_id is not None
            return await self._execute(run_id, notify_admin=True)

    async def run_manual(self) -> JobResult:
        settings = await self.repository.get_settings()
        async with self.repository.job_lock() as acquired:
            if not acquired:
                return JobResult("busy", "Другая проверка уже выполняется.")
            now = datetime.now(UTC)
            if settings.last_check_at:
                previous = settings.last_check_at.replace(
                    tzinfo=settings.last_check_at.tzinfo or UTC
                )
                elapsed = (now - previous).total_seconds()
                if elapsed < self.config.check_cooldown_seconds:
                    wait = int(self.config.check_cooldown_seconds - elapsed) + 1
                    return JobResult("rate_limited", f"Повторите через {wait} сек.")
            await self.repository.mark_check()
            key = new_delivery_key("manual")
            run_id = await self.repository.claim_delivery(key, self.config.telegram_group_id)
            assert run_id is not None
            return await self._execute(run_id, notify_admin=False)

    async def _execute(self, run_id: int, *, notify_admin: bool) -> JobResult:
        settings = await self.repository.get_settings()
        secrets = await self._secrets()
        if not secrets.token or not secrets.auth_valid:
            await self.repository.finish_delivery(
                run_id, "failed", "authorization is not configured"
            )
            return JobResult("failed", "Авторизация Money-X не настроена или истекла.")
        try:
            async with MoneyXClient(
                settings.web_url,
                settings.api_url,
                token=secrets.token,
                mxi_token=secrets.mxi_token,
                timeout=self.config.moneyx_timeout_seconds,
            ) as client:
                batch = await asyncio.wait_for(
                    collect_rates(
                        client,
                    ),
                    timeout=self.config.moneyx_total_deadline_seconds,
                )
            await self.repository.save_rates(run_id, settings.web_url, batch)
            parts = format_report(batch, settings.web_url)
            await send_report(
                self.bot,
                self.config.telegram_group_id,
                parts,  # type: ignore[arg-type]
            )
            await self.repository.finish_delivery(run_id, "sent")
            await self.repository.mark_auth_valid(secrets.token)
            for alert_key in (
                "group_unavailable",
                "group_delivery_failed",
                "auth_expired",
                "moneyx_forbidden",
                "job_timeout",
                "job_failed",
            ):
                await self.repository.reset_alert(alert_key)
            return JobResult("sent", f"Отправлено строк: {len(batch.rates)}", len(parts))
        except AuthenticationError:
            await self.repository.mark_auth_invalid(secrets.token)
            await self.repository.finish_delivery(
                run_id, "failed", "Money-X authentication expired"
            )
            if notify_admin:
                await self._alert_once(
                    "auth_expired", "Авторизация Money-X истекла. Выполните /auth_set."
                )
            return JobResult("failed", "Авторизация Money-X истекла.")
        except ForbiddenError:
            await self.repository.finish_delivery(run_id, "failed", "Money-X returned forbidden")
            if notify_admin:
                await self._alert_once(
                    "moneyx_forbidden", "Money-X отклонил запрос (403). Защита не обходилась."
                )
            return JobResult("failed", "Money-X отклонил запрос (403).")
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            safe = redact(exc)
            await self.repository.finish_delivery(run_id, "failed", safe)
            if "kicked" in safe.lower() or "chat not found" in safe.lower():
                if notify_admin:
                    await self._alert_once(
                        "group_unavailable",
                        "Бот удалён из группы или группа из TELEGRAM_GROUP_ID недоступна.",
                    )
            else:
                if notify_admin:
                    await self._alert_once(
                        "group_delivery_failed", "Telegram не принял отчёт в группу."
                    )
            return JobResult("failed", "Telegram не принял сообщение в группу.")
        except TimeoutError:
            await self.repository.finish_delivery(
                run_id, "failed", "overall Money-X deadline exceeded"
            )
            if notify_admin:
                await self._alert_once(
                    "job_timeout", "Проверка Money-X превысила общий лимит времени."
                )
            return JobResult("failed", "Превышен лимит времени проверки.")
        except Exception as exc:
            safe = redact(exc)
            logger.exception("Hourly job failed: %s", safe)
            await self.repository.finish_delivery(run_id, "failed", safe)
            if notify_admin:
                await self._alert_once(
                    "job_failed", f"Проверка Money-X завершилась ошибкой: {safe[:300]}"
                )
            return JobResult(
                "failed",
                (
                    "Проверка завершилась ошибкой; подробности отправлены администратору."
                    if notify_admin
                    else "Проверка завершилась внутренней ошибкой. Подробности записаны в журнал."
                ),
            )

    async def _alert_once(self, key: str, text: str) -> None:
        if await self.repository.claim_alert(key):
            try:
                await self.bot.send_message(self.config.admin_telegram_id, text)
            except Exception as exc:
                logger.warning("Cannot send admin alert: %s", redact(exc))
