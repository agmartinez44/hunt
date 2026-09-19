"""Quoted {50, USD, hour} FX path + CH/ES/PL × fte/freelance net tables."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hunt.core.pay import (
    QuotedPay,
    below_workspace_floor,
    derive_pay,
    engagement_label,
    estimate_pay,
    floor_month_for_workspace,
    normalize_engagement,
)
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"

FX = {"USD": 0.90, "EUR": 1.0, "PLN": 0.23, "CHF": 1.05}
FLOOR = {"amount": 7000, "currency": "EUR", "unit": "month"}
HOMES = {
    "CH": {
        "fte": {
            "income_rate": 0.16,
            "social_rate": 0.064,
            "vat_out": False,
            "source": "test CH fte",
        },
        "freelance": {
            "income_rate": 0.16,
            "social_rate": 0.10,
            "vat_out": True,
            "source": "test CH freelance",
        },
    },
    "ES": {
        "fte": {
            "income_rate": 0.24,
            "social_rate": 0.065,
            "vat_out": False,
            "source": "test ES fte",
        },
        "freelance": {
            "income_rate": 0.20,
            "social_fixed_month": 300,
            "social_fixed_currency": "EUR",
            "vat_out": True,
            "source": "test ES freelance",
        },
    },
    "PL": {
        "fte": {
            "income_rate": 0.12,
            "social_rate": 0.1371,
            "vat_out": False,
            "source": "test PL fte",
        },
        "freelance": {
            "income_rate": 0.12,
            "social_fixed_month": 400,
            "social_fixed_currency": "EUR",
            "vat_out": True,
            "source": "test PL freelance",
        },
    },
}

# 10_000 EUR / month → six cells
# CH fte:        10000 - 640 - 1600 = 7760
# CH freelance:  10000 - 1000 - 1600 = 7400
# ES fte:        10000 - 650 - 2400 = 6950
# ES freelance:  10000 - 300 - 2000 = 7700
# PL fte:        10000 - 1371 - 1200 = 7429
# PL freelance:  10000 - 400 - 1200 = 8400
SIX_CELLS = (
    ("CH", "fte", 7760.0, False, True),
    ("Switzerland", "freelance", 7400.0, True, True),
    ("ES", "fte", 6950.0, False, False),
    ("Spain", "autonomo", 7700.0, True, True),
    ("PL", "fte", 7429.0, False, True),
    ("Poland", "b2b", 8400.0, True, True),
)


def _derive(**kwargs):
    quoted = kwargs.pop("quoted", QuotedPay(amount=10000, currency="EUR", unit="month"))
    return derive_pay(
        quoted,
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates=FX,
        hours_per_month=160,
        tax_homes=HOMES,
        comp_floor=FLOOR,
        **kwargs,
    )


def test_quoted_50_usd_hour_derives():
    quoted = QuotedPay(amount=50, currency="USD", unit="hour")
    derived = derive_pay(
        quoted,
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates={"USD": 0.90, "EUR": 1.0},
        hours_per_month=160,
        tax_home="pl_jdg",
        tax_homes={"pl_jdg": {"effective_rate": 0.17}},
        comp_floor=FLOOR,
    )
    assert derived is not None
    assert derived.fx_as_of == "2026-09-18"
    assert derived.display_currency == "EUR"
    assert derived.hour == 45.0
    assert derived.day == 360.0
    assert derived.month == 7200.0
    assert derived.year == 86400.0
    assert derived.net_month == 5976.0
    assert derived.clears_floor is False


def test_quoted_50_usd_hour_uses_pl_freelance_table():
    quoted = QuotedPay(amount=50, currency="USD", unit="hour")
    derived = _derive(
        quoted=quoted,
        country="PL",
        engagement="b2b",
    )
    assert derived is not None
    assert derived.hour == 45.0
    assert derived.day == 360.0
    assert derived.month == 7200.0
    assert derived.year == 86400.0
    # 7200 - 400 social_fixed - 864 income = 5936
    assert derived.net_month == 5936.0
    assert derived.clears_floor is False


@pytest.mark.parametrize(
    "country,engagement,net,vat_out,clears",
    SIX_CELLS,
)
def test_six_tax_home_cells(country, engagement, net, vat_out, clears):
    derived = _derive(country=country, engagement=engagement)
    assert derived is not None
    assert derived.month == 10000.0
    assert derived.net_month == net
    assert derived.clears_floor is clears

    estimate = estimate_pay(
        QuotedPay(amount=10000, currency="EUR", unit="month"),
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates=FX,
        hours_per_month=160,
        country=country,
        engagement=engagement,
        tax_homes=HOMES,
        comp_floor=FLOOR,
    )
    assert estimate["net_month"] == net
    assert estimate["clears_floor"] is clears
    assert estimate["assumptions"]["disclaimer"].startswith("Estimate")
    assert estimate["assumptions"]["vat_out"] is vat_out
    assert estimate["assumptions"]["rate_source"]
    assert estimate["country"] in {"CH", "ES", "PL"}
    assert estimate["engagement"] in {"fte", "freelance"}


def test_engagement_label_fte_freelance_unknown():
    assert engagement_label("fte") == "FTE"
    assert engagement_label("uop") == "FTE"
    assert engagement_label("full-time") == "FTE"
    assert engagement_label("b2b") == "Freelance"
    assert engagement_label("freelance") == "Freelance"
    assert engagement_label("contract") == "Freelance"
    assert engagement_label(None) == "Unknown"
    assert engagement_label("") == "Unknown"
    assert engagement_label("part-time") == "Unknown"
    assert normalize_engagement("FULL TIME") == "fte"
    assert normalize_engagement("B2B") == "freelance"


def test_country_and_engagement_enough_without_tax_home():
    """Google SRE shape: PL FTE, PLN/year, empty tax_home_for_net."""
    quoted = QuotedPay(amount=364000, currency="PLN", unit="year")
    derived = _derive(quoted=quoted, country="PL", engagement="fte")
    assert derived is not None
    assert derived.month == 6976.0
    assert derived.net_month == 5182.47
    assert derived.clears_floor is False
    estimate = estimate_pay(
        quoted,
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates=FX,
        hours_per_month=160,
        country="PL",
        engagement="fte",
        tax_homes=HOMES,
        comp_floor=FLOOR,
    )
    assert estimate["assumptions"]["home"] == "PL.fte"
    assert estimate["assumptions"]["tax_home"] is None


def test_legacy_tax_home_maps_onto_table():
    derived = _derive(tax_home="pl_jdg")
    assert derived is not None
    assert derived.net_month == 8400.0


def test_no_fx_stamp_means_no_derived():
    quoted = QuotedPay(amount=50, currency="USD", unit="hour")
    assert (
        derive_pay(
            quoted,
            display_currency="EUR",
            fx_as_of=None,
            fx_rates={"USD": 0.90},
        )
        is None
    )


def test_missing_rate_means_no_derived():
    quoted = QuotedPay(amount=50, currency="USD", unit="hour")
    assert (
        derive_pay(
            quoted,
            display_currency="EUR",
            fx_as_of="2026-09-18",
            fx_rates={"PLN": 0.23},
        )
        is None
    )


def test_same_currency_does_not_need_a_rate_row():
    quoted = QuotedPay(amount=55, currency="EUR", unit="hour")
    derived = derive_pay(
        quoted,
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates={},
        hours_per_month=160,
        tax_home="unknown",
        tax_homes={"unknown": {}},
        comp_floor=FLOOR,
    )
    assert derived is not None
    assert derived.hour == 55.0
    assert derived.month == 8800.0
    assert derived.net_month is None
    assert derived.clears_floor is None


def test_year_quote_uses_160h_months():
    quoted = QuotedPay(amount=96000, currency="EUR", unit="year")
    derived = derive_pay(
        quoted,
        display_currency="EUR",
        fx_as_of="2026-09-18",
        fx_rates={"EUR": 1.0},
        hours_per_month=160,
    )
    assert derived is not None
    assert derived.month == 8000.0
    assert derived.hour == 50.0
    assert derived.day == 400.0


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(EXAMPLE, data, ignore=shutil.ignore_patterns("attachments"))
    return data


def test_floor_month_for_workspace_ignores_tax_cell(workspace):
    with Workspace.open(workspace) as ws:
        assert floor_month_for_workspace(ws) == 7000.0
        quoted = QuotedPay(amount=22000, currency="PLN", unit="month")
        estimate = estimate_pay(
            quoted,
            display_currency=ws.display_currency,
            fx_as_of=ws.fx_as_of,
            fx_rates=ws.fx_rates,
            hours_per_month=ws.hours_per_month,
            country=None,
            engagement=None,
            tax_homes=ws.tax_homes,
            comp_floor=ws.comp_floor,
        )
        assert estimate["assumptions"].get("floor_month") is None
        derived = derive_pay(
            quoted,
            display_currency=ws.display_currency,
            fx_as_of=ws.fx_as_of,
            fx_rates=ws.fx_rates,
            hours_per_month=ws.hours_per_month,
            country=None,
            engagement=None,
            tax_homes=ws.tax_homes,
            comp_floor=ws.comp_floor,
        )
        assert derived is not None
        # 22000 PLN × 0.23 via hour rounding: 31.62 × 160 = 5059.20 EUR/month
        assert derived.month == 5059.2
        assert derived.month < 7000
        assert derived.clears_floor is None
        assert below_workspace_floor(ws, derived) is True
        ws.config["tax_homes"] = {}
        assert floor_month_for_workspace(ws) == 7000.0
        assert below_workspace_floor(ws, derived) is True
