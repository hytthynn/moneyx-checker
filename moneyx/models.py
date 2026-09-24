from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaError(ValueError):
    pass


class CryptoPair(BaseModel):
    model_config = ConfigDict(extra="allow")

    currency: str
    network: str
    rate: Decimal | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("crypto item is not an object")
        data = dict(value)
        data["currency"] = data.get("currency") or data.get("code") or data.get("coin")
        data["network"] = data.get("network") or data.get("network_code") or data.get("chain")
        if data.get("rate") is None:
            data["rate"] = data.get("course") or data.get("exchange_rate")
        return data


class AuthUser(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | str | None = None


class AuthResult(BaseModel):
    user: AuthUser


class RateResult(BaseModel):
    currency: str
    network: str
    rate: Decimal
    source: str = Field(pattern="^(list|wallet)$")


class RateFailure(BaseModel):
    currency: str
    network: str
    error: str


class RateBatch(BaseModel):
    rates: list[RateResult]
    failures: list[RateFailure] = Field(default_factory=list)


def as_decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SchemaError("rate is not a decimal") from exc
    if not result.is_finite() or result <= 0:
        raise SchemaError("rate must be a positive finite number")
    return result
