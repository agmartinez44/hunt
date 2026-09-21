"""LinkedIn alert parse: header-only subject fallback vs body cards."""

from __future__ import annotations

from pathlib import Path

from hunt.adapters.linkedin_alert import (
    extract_engagement,
    parse_alert,
    parse_html_cards,
    parse_rfc822,
    parse_subject,
)
from hunt.core.inbox import add_item
from hunt.core.listings import backfill_engagement, get_listing
from hunt.core.workspace import Workspace

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
        "kind": "fixed",
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
        "kind": "fixed",
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


def _card_text(blob: str) -> str:
    return (
        "Staff SRE\n"
        "Acme Radar\n"
        "Warsaw, Poland (Remote)\n"
        f"{blob}\n"
        "View job: https://www.linkedin.com/jobs/view/4290000009\n"
    )


def test_engagement_extracted_when_posting_states_it():
    cases = [
        ("Full-time", "fte"),
        ("Full\u2011time", "fte"),
        ("FULL TIME", "fte"),
        ("permanent", "fte"),
        ("B2B", "b2b"),
        ("freelance", "b2b"),
        ("Contract", "b2b"),
        ("contractor", "b2b"),
    ]
    for blob, expected in cases:
        parsed = parse_alert("Acme Radar is hiring a Staff SRE", text=_card_text(blob))
        assert parsed.get("engagement") == expected, blob


def test_engagement_not_invented_when_unstated():
    parsed = parse_alert("Acme Radar is hiring a Staff SRE", text=_card_text("Mid-Senior level"))
    assert "engagement" not in parsed
    assert extract_engagement("Staff SRE at Acme Radar in Warsaw") is None
    assert extract_engagement("part-time") is None


def test_html_card_engagement_from_body_and_aria_label():
    html = (
        '<a href="https://www.linkedin.com/jobs/view/77">Staff SRE</a>'
        "<p>Acme Radar</p><p>Remote</p><p>Full-time</p>"
    )
    cards = parse_html_cards(html)
    assert cards[0]["engagement"] == "fte"
    labeled = parse_html_cards(
        '<a href="https://www.linkedin.com/jobs/view/78" '
        'aria-label="Staff SRE at Acme · B2B">Staff SRE</a>'
    )
    assert labeled[0]["engagement"] == "b2b"


def test_backfill_engagement_from_stated_text_only(tmp_path):
    from pathlib import Path
    import shutil

    root = Path(__file__).resolve().parent.parent
    data = tmp_path / "workspace"
    shutil.copytree(root / "example-workspace", data, ignore=shutil.ignore_patterns("attachments"))
    with Workspace.open(data) as ws:
        stated = add_item(
            ws,
            company="Acme Radar",
            title="Staff SRE",
            url="https://www.linkedin.com/jobs/view/1",
            payload={
                "company": "Acme Radar",
                "title_posted": "Staff SRE",
                "location_line": "Warsaw, Poland (Remote) · Full-time",
            },
        )
        silent = add_item(
            ws,
            company="Widget Labs",
            title="Reliability Engineer",
            url="https://www.linkedin.com/jobs/view/2",
            payload={"company": "Widget Labs", "title_posted": "Reliability Engineer"},
        )
        result = backfill_engagement(ws)
        stated_listing = get_listing(ws, stated.listing_id)
        silent_listing = get_listing(ws, silent.listing_id)

    assert result["updated"] == 1
    assert stated_listing.payload.get("engagement") == "fte"
    assert "engagement" not in silent_listing.payload


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
