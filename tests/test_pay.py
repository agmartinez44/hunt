"""Quoted {50, USD, hour} derives to FX-stamped display-currency fields."""

from __future__ import annotations

from hunt.core.pay import QuotedPay, derive_pay


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
        comp_floor={"amount": 7000, "currency": "EUR", "unit": "month"},
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
        comp_floor={"amount": 7000, "currency": "EUR", "unit": "month"},
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
