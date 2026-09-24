from __future__ import annotations

import hashlib
import hmac
import logging
import re
from base64 import urlsafe_b64encode

from cryptography.fernet import Fernet, InvalidToken


class EncryptionError(RuntimeError):
    pass


class SecretBox:
    def __init__(self, key: str):
        if not key:
            raise EncryptionError("APP_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise EncryptionError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc

    @classmethod
    def from_passphrase(cls, passphrase: str) -> "SecretBox":
        digest = hashlib.sha256(passphrase.encode()).digest()
        return cls(urlsafe_b64encode(digest).decode())

    def encrypt(self, value: str | None) -> str | None:
        return self._fernet.encrypt(value.encode()).decode() if value else None

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise EncryptionError(
                "Stored secret cannot be decrypted with the configured key"
            ) from exc


def secure_equals(actual: str | None, expected: str | None) -> bool:
    return bool(actual and expected) and hmac.compare_digest(actual, expected)


def mask_secret(value: str | None) -> str:
    if not value:
        return "не задан"
    if len(value) < 8:
        return "***"
    return f"{value[:3]}…{value[-2:]}"


_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_COOKIE_RE = re.compile(r"(?i)((?:token|mxi_token)=)[^;\s]+")
_WALLET_RE = re.compile(r'(?i)("(?:address|wallet|qr_code)"\s*:\s*")[^"]+')
_TELEGRAM_TOKEN_RE = re.compile(r"(?i)(?:bot)?\d{8,}:[A-Za-z0-9_-]{20,}")


def redact(value: object) -> str:
    text = str(value)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _COOKIE_RE.sub(r"\1[REDACTED]", text)
    text = _WALLET_RE.sub(r"\1[REDACTED]", text)
    return _TELEGRAM_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: _redact_log_arg(value) for key, value in record.args.items()}
        elif record.args:
            record.args = tuple(_redact_log_arg(arg) for arg in record.args)
        return True


def _redact_log_arg(value: object) -> object:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(value)
