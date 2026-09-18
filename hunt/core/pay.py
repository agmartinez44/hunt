"""Quoted pay → FX-stamped derived fields.

Conversions are code. No LLM nets. Derived fields are omitted unless an
FX stamp (``fx_as_of``) and a rate into the workspace display currency
are available.

Unit bridge (spec): day ÷ 8 hours; month = ``hours_per_month`` hours
(default 160); year = 12 months.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

HOURS_PER_DAY = 8.0
DEFAULT_HOURS_PER_MONTH = 160.0
PAY_UNITS = ("hour", "day", "month", "year")


@dataclass(frozen=True)
class QuotedPay:
    amount: float
    currency: str
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", self.currency.upper())
        object.__setattr__(self, "unit", self.unit.lower())
        if self.unit not in PAY_UNITS:
            raise ValueError(f"comp unit must be one of {PAY_UNITS}, got {self.unit!r}")
        if self.amount < 0:
            raise ValueError("comp amount must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class DerivedPay:
    display_currency: str
    fx_as_of: str
    hour: float
    day: float
    month: float
    year: float
    net_month: float | None
    clears_floor: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_currency": self.display_currency,
            "fx_as_of": self.fx_as_of,
            "hour": self.hour,
            "day": self.day,
            "month": self.month,
            "year": self.year,
            "net_month": self.net_month,
            "clears_floor": self.clears_floor,
        }


def _money(value: float) -> float:
    return round(float(value), 2)


def _fx_rate(
    currency: str,
    display_currency: str,
    rates: Mapping[str, float],
) -> float | None:
    src = currency.upper()
    dst = display_currency.upper()
    if src == dst:
        return 1.0
    if src in rates:
        return float(rates[src])
    return None


def _gross_hour(
    quoted: QuotedPay,
    *,
    rate: float,
    hours_per_month: float,
) -> float:
    amount = quoted.amount * rate
    if quoted.unit == "hour":
        return amount
    if quoted.unit == "day":
        return amount / HOURS_PER_DAY
    if quoted.unit == "month":
        return amount / hours_per_month
    return amount / (hours_per_month * 12.0)


def _floor_month(
    comp_floor: Mapping[str, Any] | None,
    *,
    display_currency: str,
    rates: Mapping[str, float],
    hours_per_month: float,
) -> float | None:
    if not comp_floor:
        return None
    amount = comp_floor.get("amount")
    if amount is None:
        return None
    currency = str(comp_floor.get("currency") or display_currency).upper()
    unit = str(comp_floor.get("unit") or "month").lower()
    rate = _fx_rate(currency, display_currency, rates)
    if rate is None:
        return None
    display_amount = float(amount) * rate
    if unit == "month":
        return _money(display_amount)
    if unit == "year":
        return _money(display_amount / 12.0)
    if unit == "hour":
        return _money(display_amount * hours_per_month)
    if unit == "day":
        return _money(display_amount / HOURS_PER_DAY * hours_per_month)
    return None


def derive_pay(
    quoted: QuotedPay | None,
    *,
    display_currency: str,
    fx_as_of: str | None,
    fx_rates: Mapping[str, float] | None,
    hours_per_month: float = DEFAULT_HOURS_PER_MONTH,
    tax_home: str | None = None,
    tax_homes: Mapping[str, Any] | None = None,
    comp_floor: Mapping[str, Any] | None = None,
) -> DerivedPay | None:
    """Return stamped derived pay, or ``None`` when FX cannot be applied."""
    if quoted is None or not fx_as_of:
        return None
    rates = {str(k).upper(): float(v) for k, v in (fx_rates or {}).items()}
    display = display_currency.upper()
    rate = _fx_rate(quoted.currency, display, rates)
    if rate is None:
        return None

    hours_per_month = float(hours_per_month or DEFAULT_HOURS_PER_MONTH)
    hour = _money(_gross_hour(quoted, rate=rate, hours_per_month=hours_per_month))
    day = _money(hour * HOURS_PER_DAY)
    month = _money(hour * hours_per_month)
    year = _money(month * 12.0)

    net_month: float | None = None
    homes = tax_homes or {}
    home_key = (tax_home or "").strip()
    spec = homes.get(home_key) if home_key else None
    if isinstance(spec, Mapping) and spec.get("effective_rate") is not None:
        effective = float(spec["effective_rate"])
        net_month = _money(month * (1.0 - effective))

    clears: bool | None = None
    floor_month = _floor_month(
        comp_floor,
        display_currency=display,
        rates=rates,
        hours_per_month=hours_per_month,
    )
    if net_month is not None and floor_month is not None:
        clears = net_month >= floor_month

    return DerivedPay(
        display_currency=display,
        fx_as_of=str(fx_as_of),
        hour=hour,
        day=day,
        month=month,
        year=year,
        net_month=net_month,
        clears_floor=clears,
    )
