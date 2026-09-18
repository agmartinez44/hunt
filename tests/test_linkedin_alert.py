"""LinkedIn alert parse: header-only subject fallback vs body cards."""

from __future__ import annotations

from pathlib import Path

from hunt.adapters.linkedin_alert import parse_alert, parse_rfc822, parse_subject

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_subject_hiring_is_fallback_not_mailbox_name():
    parsed = parse_subject("Acme Radar is hiring a Staff SRE")
    assert parsed["company"] == "Acme Radar"
    assert parsed["title"] == "Staff SRE"
    assert "comp_quoted" not in parsed


def test_subject_at_with_quoted_pay():
    parsed = parse_subject("SRE at ExampleCorp: up to EUR 10K/month")
    assert parsed["company"] == "ExampleCorp"
    assert parsed["title"] == "SRE"
    assert parsed["comp_quoted"] == {
        "amount": 10000.0,
        "currency": "EUR",
        "unit": "month",
    }


def test_header_only_eml_uses_subject():
    raw = (FIXTURES / "linkedin_alert_headers_only.eml").read_bytes()
    parsed = parse_rfc822(raw)
    assert parsed["company"] == "Acme Radar"
    assert parsed["title"] == "Staff SRE"
    assert "url" not in parsed
    assert "comp_quoted" not in parsed
    assert "location_city" not in parsed


def test_body_eml_beats_subject_and_extracts_canonical_fields():
    raw = (FIXTURES / "linkedin_alert_body.eml").read_bytes()
    parsed = parse_rfc822(raw)
    assert parsed["company"] == "Acme Radar"
    assert parsed["title"] == "Staff SRE"
    assert parsed["url"] == "https://www.linkedin.com/jobs/view/4290000001"
    assert parsed["location_city"] == "Warsaw"
    assert parsed["location_country"] == "Poland"
    assert parsed["modality"] == "remote"
    assert parsed["comp_quoted"] == {
        "amount": 90000.0,
        "currency": "EUR",
        "unit": "year",
    }


def test_body_does_not_invent_pay():
    raw = (FIXTURES / "linkedin_alert_no_pay.eml").read_bytes()
    parsed = parse_rfc822(raw)
    assert parsed["company"] == "Widget Labs"
    assert parsed["title"] == "Reliability Engineer"
    assert parsed["url"] == "https://www.linkedin.com/jobs/view/4290000003"
    assert parsed["location_city"] == "Zurich"
    assert parsed["location_country"] == "Switzerland"
    assert parsed["modality"] == "hybrid"
    assert parsed["engagement"] == "fte"
    assert "comp_quoted" not in parsed


def test_unparsed_subject_does_not_use_mailbox_when_body_has_employer():
    parsed = parse_alert(
        "12 new jobs for you",
        text=(
            "Your job alert for sre\n"
            "New jobs matching your alert\n\n"
            "Staff SRE\n"
            "Northwind Platform\n"
            "Remote\n\n"
            "View job: https://www.linkedin.com/comm/jobs/view/42/\n"
        ),
        company_default="LinkedIn",
    )
    assert parsed["company"] == "Northwind Platform"
    assert parsed["title"] == "Staff SRE"
    assert parsed["url"] == "https://www.linkedin.com/jobs/view/42"
    assert parsed["modality"] == "remote"
