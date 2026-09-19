"""Quoted pay → FX-stamped derived fields.

Conversions are code. No LLM nets. Derived fields are omitted unless an
FX stamp (``fx_as_of``) and a rate into the workspace display currency
are available.

Unit bridge (spec): day ÷ 8 hours; month = ``hours_per_month`` hours
(default 160); year = 12 months.

Net / month uses workspace ``tax_homes`` tables keyed by country
(CH|ES|PL) × engagement (fte|freelance). Estimates, not tax advice.
``comp_floor`` / ``clears_floor`` stay in config and derived JSON for
agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from hunt.core.workspace import Workspace

HOURS_PER_DAY = 8.0
DEFAULT_HOURS_PER_MONTH = 160.0
PAY_UNITS = ("hour", "day", "month", "year")
ENGAGEMENT_KINDS = ("fte", "freelance")

_COUNTRY_ALIASES = {
    "CH": "CH",
    "CHE": "CH",
    "SWITZERLAND": "CH",
    "SWISS": "CH",
    "SUISSE": "CH",
    "SCHWEIZ": "CH",
    "SVIZZERA": "CH",
    "ES": "ES",
    "ESP": "ES",
    "SPAIN": "ES",
    "ESPANA": "ES",
    "ESPAÑA": "ES",
    "PL": "PL",
    "POL": "PL",
    "POLAND": "PL",
    "POLSKA": "PL",
}

_ENGAGEMENT_ALIASES = {
    "FTE": "fte",
    "PERMANENT": "fte",
    "EMPLOYMENT": "fte",
    "EMPLOYEE": "fte",
    "UOP": "fte",
    "FULL-TIME": "fte",
    "FULLTIME": "fte",
    "FREELANCE": "freelance",
    "FREELANCER": "freelance",
    "B2B": "freelance",
    "JDG": "freelance",
    "AUTONOMO": "freelance",
    "AUTÓNOMO": "freelance",
    "CONTRACT": "freelance",
    "CONTRACTOR": "freelance",
    "CONTRACTING": "freelance",
    "SELF-EMPLOYED": "freelance",
    "SELFEMPLOYED": "freelance",
    "SOLETRADER": "freelance",
    "SOLE-TRADER": "freelance",
}

# Historical tax_home_for_net keys → country + engagement.
_LEGACY_HOMES = {
    "pl_jdg": ("PL", "freelance"),
    "es_autonomo": ("ES", "freelance"),
    "ch_fte": ("CH", "fte"),
}

_LEGACY_BY_CELL = {cell: key for key, cell in _LEGACY_HOMES.items()}

DISCLAIMER = "Estimate, not tax advice. Conversions are code, never an LLM net."


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


def floor_month_for_workspace(ws: Workspace) -> float | None:
    """Workspace comp_floor in display-currency / month, or None if floor/FX missing.

    Wraps ``_floor_month`` (comp_floor + FX only). Does not read
    ``assumptions.floor_month`` and does not need a tax cell.
    """
    return _floor_month(
        ws.comp_floor,
        display_currency=ws.display_currency,
        rates=ws.fx_rates,
        hours_per_month=ws.hours_per_month,
    )


def below_workspace_floor(ws: Workspace, derived: DerivedPay | None) -> bool:
    """True when Hunt can prove quoted gross (or net) is below the workspace floor.

    Never true when quote or FX is missing (derived is None).
    ``clears_floor is None`` is not a veto when stamped gross month is below floor.
    Gross uses the same 'below' as net: not ``>=`` floor, i.e. ``month < floor``.
    Exact equality (gross month == floor) does **not** fire ``pay_below_floor``.
    """
    if derived is None:
        return False
    if derived.clears_floor is False:
        return True
    floor = floor_month_for_workspace(ws)
    if derived.month is not None and floor is not None and derived.month < floor:
        return True
    return False


def normalize_country(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = text.upper().replace(".", "")
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    folded = (
        text.strip()
        .replace("á", "a")
        .replace("ñ", "n")
        .upper()
        .replace(".", "")
    )
    return _COUNTRY_ALIASES.get(folded)


def normalize_engagement(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = text.upper().replace(" ", "").replace("_", "-")
    mapped = _ENGAGEMENT_ALIASES.get(key)
    if mapped:
        return mapped
    lowered = text.lower()
    if lowered in ENGAGEMENT_KINDS:
        return lowered
    return None


def engagement_label(value: str | None) -> str:
    """Human inbox label. Canonical kinds are FTE / Freelance; missing is Unknown."""
    kind = normalize_engagement(value)
    if kind == "fte":
        return "FTE"
    if kind == "freelance":
        return "Freelance"
    return "Unknown"


def _is_rate_spec(spec: Any) -> bool:
    if not isinstance(spec, Mapping):
        return False
    return any(
        spec.get(key) is not None
        for key in (
            "effective_rate",
            "income_rate",
            "social_rate",
            "social_fixed_month",
        )
    )


def _legacy_cell(tax_home: str | None) -> tuple[str, str] | None:
    if not tax_home:
        return None
    key = str(tax_home).strip()
    if key in _LEGACY_HOMES:
        return _LEGACY_HOMES[key]
    lowered = key.lower().replace("-", "_")
    return _LEGACY_HOMES.get(lowered)


def _country_block(
    homes: Mapping[str, Any], country: str | None
) -> Mapping[str, Any] | None:
    if not country:
        return None
    for key, block in homes.items():
        if _is_rate_spec(block):
            continue
        if normalize_country(str(key)) == country and isinstance(block, Mapping):
            return block
    return None


def resolve_tax_home(
    homes: Mapping[str, Any] | None,
    *,
    country: str | None = None,
    engagement: str | None = None,
    tax_home: str | None = None,
) -> dict[str, Any]:
    """Pick a workspace tax-home cell from country + engagement.

    ``tax_home_for_net`` remains an optional override (legacy key, country
    code, or ``CH.fte``-style cell). Country + engagement is enough.
    """
    homes = homes or {}
    raw_home = (tax_home or "").strip() or None
    legacy = _legacy_cell(raw_home)

    resolved_country = normalize_country(raw_home) or (
        legacy[0] if legacy else None
    ) or normalize_country(country)
    resolved_engagement = None
    if raw_home and "." in raw_home:
        _, _, tail = raw_home.partition(".")
        resolved_engagement = normalize_engagement(tail)
    if resolved_engagement is None and legacy:
        resolved_engagement = legacy[1]
    if resolved_engagement is None:
        resolved_engagement = normalize_engagement(engagement)

    spec: Mapping[str, Any] | None = None
    home_key: str | None = None

    if raw_home and raw_home in homes and _is_rate_spec(homes.get(raw_home)):
        spec = homes[raw_home]  # type: ignore[assignment]
        home_key = raw_home
        if legacy:
            resolved_country = resolved_country or legacy[0]
            resolved_engagement = resolved_engagement or legacy[1]
    elif resolved_country and resolved_engagement:
        block = _country_block(homes, resolved_country)
        cell = block.get(resolved_engagement) if block else None
        if _is_rate_spec(cell):
            spec = cell  # type: ignore[assignment]
            home_key = f"{resolved_country}.{resolved_engagement}"
        else:
            legacy_key = _LEGACY_BY_CELL.get(
                (resolved_country, resolved_engagement)
            )
            if legacy_key and _is_rate_spec(homes.get(legacy_key)):
                spec = homes[legacy_key]  # type: ignore[assignment]
                home_key = legacy_key

    return {
        "country": resolved_country,
        "engagement": resolved_engagement,
        "home_key": home_key,
        "spec": dict(spec) if isinstance(spec, Mapping) else None,
        "tax_home": raw_home,
    }


def _apply_table(
    gross_month: float,
    spec: Mapping[str, Any],
    *,
    display_currency: str,
    rates: Mapping[str, float],
) -> tuple[float | None, dict[str, Any]]:
    assumptions: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "income_rate": None,
        "income_base": "gross",
        "social_rate": 0.0,
        "social_fixed_month": 0.0,
        "social_fixed_currency": display_currency,
        "vat_out": bool(spec.get("vat_out", False)),
        "rate_source": spec.get("source") or spec.get("rate_source"),
        "legacy_effective_rate": None,
    }

    if spec.get("income_rate") is None and spec.get("social_rate") is None:
        if spec.get("social_fixed_month") is None and spec.get("effective_rate") is not None:
            effective = float(spec["effective_rate"])
            assumptions["legacy_effective_rate"] = effective
            assumptions["rate_source"] = assumptions["rate_source"] or (
                "tax_homes.effective_rate"
            )
            return _money(gross_month * (1.0 - effective)), assumptions

    income_rate = float(spec.get("income_rate") or 0.0)
    social_rate = float(spec.get("social_rate") or 0.0)
    income_base = str(spec.get("income_base") or "gross").lower()
    if income_base not in {"gross", "after_social"}:
        income_base = "gross"

    fixed_amount = float(spec.get("social_fixed_month") or 0.0)
    fixed_currency = str(
        spec.get("social_fixed_currency") or display_currency
    ).upper()
    fixed_rate = _fx_rate(fixed_currency, display_currency, rates)
    if fixed_amount and fixed_rate is None:
        assumptions["missing"] = (
            f"no FX rate for social_fixed_currency {fixed_currency}"
        )
        return None, assumptions

    social_variable = _money(gross_month * social_rate)
    social_fixed = _money(fixed_amount * (fixed_rate or 0.0)) if fixed_amount else 0.0
    social = _money(social_variable + social_fixed)
    taxable = gross_month - social if income_base == "after_social" else gross_month
    income = _money(taxable * income_rate)
    net = _money(gross_month - social - income)

    assumptions.update(
        {
            "income_rate": income_rate,
            "income_base": income_base,
            "social_rate": social_rate,
            "social_fixed_month": fixed_amount,
            "social_fixed_currency": fixed_currency,
            "social_month": social,
            "income_month": income,
        }
    )
    if spec.get("effective_rate") is not None and spec.get("income_rate") is None:
        assumptions["legacy_effective_rate"] = float(spec["effective_rate"])
    return net, assumptions


def estimate_pay(
    quoted: QuotedPay | None,
    *,
    display_currency: str,
    fx_as_of: str | None,
    fx_rates: Mapping[str, float] | None,
    hours_per_month: float = DEFAULT_HOURS_PER_MONTH,
    country: str | None = None,
    engagement: str | None = None,
    tax_home: str | None = None,
    tax_homes: Mapping[str, Any] | None = None,
    comp_floor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Country + engagement → net / month in display currency.

    Same function the board uses when stamping ``comp_derived.net_month``.
    """
    display = display_currency.upper()
    hours_per_month = float(hours_per_month or DEFAULT_HOURS_PER_MONTH)
    rates = {str(k).upper(): float(v) for k, v in (fx_rates or {}).items()}
    resolved = resolve_tax_home(
        tax_homes,
        country=country,
        engagement=engagement,
        tax_home=tax_home,
    )
    assumptions: dict[str, Any] = {
        "disclaimer": DISCLAIMER,
        "home": resolved["home_key"],
        "country": resolved["country"],
        "engagement": resolved["engagement"],
        "tax_home": resolved["tax_home"],
        "fx_as_of": fx_as_of,
        "hours_per_month": hours_per_month,
        "vat_out": None,
        "rate_source": None,
    }

    payload: dict[str, Any] = {
        "quoted": quoted.to_dict() if quoted else None,
        "country": resolved["country"],
        "engagement": resolved["engagement"],
        "display_currency": display,
        "fx_as_of": fx_as_of,
        "gross": None,
        "net_month": None,
        "clears_floor": None,
        "assumptions": assumptions,
    }

    if quoted is None:
        assumptions["missing"] = "quoted pay is required"
        return payload
    if not fx_as_of:
        assumptions["missing"] = "no FX stamp (fx.as_of)"
        return payload

    rate = _fx_rate(quoted.currency, display, rates)
    if rate is None:
        assumptions["missing"] = f"no FX rate for {quoted.currency} → {display}"
        return payload

    hour = _money(_gross_hour(quoted, rate=rate, hours_per_month=hours_per_month))
    day = _money(hour * HOURS_PER_DAY)
    month = _money(hour * hours_per_month)
    year = _money(month * 12.0)
    payload["gross"] = {
        "hour": hour,
        "day": day,
        "month": month,
        "year": year,
    }
    assumptions["fx_rate"] = rate
    assumptions["fx_rates_used"] = {quoted.currency: rate}

    spec = resolved["spec"]
    if not spec:
        if not resolved["country"] or not resolved["engagement"]:
            assumptions["missing"] = (
                "country + engagement (or tax_home) required to estimate net"
            )
        else:
            assumptions["missing"] = (
                f"no tax_homes cell for {resolved['country']}."
                f"{resolved['engagement']}"
            )
        return payload

    net, table_assumptions = _apply_table(
        month,
        spec,
        display_currency=display,
        rates=rates,
    )
    assumptions.update(table_assumptions)
    assumptions["home"] = resolved["home_key"]
    payload["net_month"] = net

    floor_month = _floor_month(
        comp_floor,
        display_currency=display,
        rates=rates,
        hours_per_month=hours_per_month,
    )
    assumptions["floor_month"] = floor_month
    if net is not None and floor_month is not None:
        payload["clears_floor"] = net >= floor_month

    return payload


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
    country: str | None = None,
    engagement: str | None = None,
) -> DerivedPay | None:
    """Return stamped derived pay, or ``None`` when FX cannot be applied."""
    estimate = estimate_pay(
        quoted,
        display_currency=display_currency,
        fx_as_of=fx_as_of,
        fx_rates=fx_rates,
        hours_per_month=hours_per_month,
        country=country,
        engagement=engagement,
        tax_home=tax_home,
        tax_homes=tax_homes,
        comp_floor=comp_floor,
    )
    gross = estimate.get("gross")
    if not estimate.get("fx_as_of") or not isinstance(gross, dict):
        return None
    return DerivedPay(
        display_currency=str(estimate["display_currency"]),
        fx_as_of=str(estimate["fx_as_of"]),
        hour=gross["hour"],
        day=gross["day"],
        month=gross["month"],
        year=gross["year"],
        net_month=estimate["net_month"],
        clears_floor=estimate["clears_floor"],
    )
