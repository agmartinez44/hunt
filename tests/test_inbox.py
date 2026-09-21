"""Inbox serialize-then-sort. SQL list_inbox stays created_at DESC."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from hunt.core.errors import ValidationError
from hunt.core.inbox import (
    INBOX_SORT_KEYS,
    add_item,
    list_inbox,
    resolve_inbox_sort,
    serialize_inbox_item,
    serialize_inbox_list,
    sort_serialized_inbox,
    work_location_label,
)
from hunt.core.sources import list_sources
from hunt.core.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "example-workspace"


@pytest.fixture
def workspace(tmp_path: Path):
    data = tmp_path / "workspace"
    shutil.copytree(
        EXAMPLE, data, ignore=shutil.ignore_patterns("attachments", "store.sqlite")
    )
    env = {**os.environ, "HUNT_DATA": str(data), "PYTHONPATH": str(ROOT)}
    return data, env


def test_resolve_inbox_sort_defaults():
    assert resolve_inbox_sort(None, None) == ("created_at", "desc")
    assert resolve_inbox_sort("", "") == ("created_at", "desc")
    assert resolve_inbox_sort("company", None) == ("company", "asc")
    assert resolve_inbox_sort("role", None) == ("role", "asc")
    assert resolve_inbox_sort("source_id", None) == ("source_id", "asc")
    assert resolve_inbox_sort("gross_month", None) == ("gross_month", "desc")
    assert resolve_inbox_sort("created_at", None) == ("created_at", "desc")
    assert resolve_inbox_sort("company", "desc") == ("company", "desc")
    with pytest.raises(ValidationError, match="inbox sort"):
        resolve_inbox_sort("nope", None)
    with pytest.raises(ValidationError, match="inbox order"):
        resolve_inbox_sort("company", "sideways")


def test_sort_serialized_inbox_missing_gross_last():
    rows = [
        {"company": "A", "gross_month": 100, "created_at": "2026-01-01T00:00:03Z"},
        {"company": "B", "gross_month": None, "created_at": "2026-01-01T00:00:02Z"},
        {"company": "C", "gross_month": 50, "created_at": "2026-01-01T00:00:01Z"},
        {"company": "D", "gross_month": None, "created_at": "2026-01-01T00:00:00Z"},
    ]
    desc = sort_serialized_inbox(rows, sort="gross_month")
    assert [r["company"] for r in desc] == ["A", "C", "B", "D"]
    asc = sort_serialized_inbox(rows, sort="gross_month", order="asc")
    assert [r["company"] for r in asc] == ["C", "A", "B", "D"]
    empty = sort_serialized_inbox(
        [{"company": "E", "gross_month": "", "created_at": "x"}],
        sort="gross_month",
        order="desc",
    )
    assert [r["company"] for r in empty] == ["E"]


def test_sort_serialized_inbox_text_and_stability():
    rows = [
        {"company": "Beta", "role": "SRE", "source_id": "z", "created_at": "3"},
        {"company": "alpha", "role": "SWE", "source_id": "a", "created_at": "2"},
        {"company": "Alpha", "role": "PM", "source_id": "m", "created_at": "1"},
    ]
    by_company = sort_serialized_inbox(rows, sort="company")
    assert [r["source_id"] for r in by_company] == ["a", "m", "z"]
    by_role = sort_serialized_inbox(rows, sort="role")
    assert [r["role"] for r in by_role] == ["PM", "SRE", "SWE"]


def test_serialize_inbox_list_does_not_change_list_inbox(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        list_sources(ws)
        ws.conn.execute("DELETE FROM inbox_items")
        ws.conn.execute("DELETE FROM listings")
        ws.conn.commit()
        add_item(ws, company="Zulu Co", title="Z", source_id="justjoin-sample", external_id="u-z")
        add_item(ws, company="Alpha Co", title="A", source_id="remotive-eu", external_id="u-a")
        raw = list_inbox(ws)
        companies = [i.company for i in raw]
        sorted_rows = serialize_inbox_list(ws, raw, sort="company")
        assert [r["company"] for r in sorted_rows] == sorted(companies)
        again = list_inbox(ws)
        assert [i.company for i in again] == companies
        assert INBOX_SORT_KEYS == (
            "created_at",
            "company",
            "role",
            "source_id",
            "gross_month",
        )


def test_work_location_label_remote_vs_hq():
    remote_hq, title = work_location_label(
        {
            "modality": "remote",
            "location_city": "Warszawa",
            "location_country": "PL",
        }
    )
    assert remote_hq == "Remote · PL"
    assert title == "Warszawa"
    europe, europe_title = work_location_label(
        {"modality": "remote", "location_city": "Europe"}
    )
    assert europe == "Remote · Europe"
    assert europe_title is None
    poland, poland_title = work_location_label(
        {
            "modality": "remote",
            "location_city": "Poland",
            "location_country": "PL",
        }
    )
    assert poland == "Remote · Poland"
    assert poland_title is None
    bare, bare_title = work_location_label(
        {"modality": "remote", "location_city": "Kraków"}
    )
    assert bare == "Remote"
    assert bare_title == "Kraków"
    hybrid_days, _ = work_location_label(
        {
            "modality": "hybrid",
            "location_city": "Kraków",
            "office_days_per_week": 3,
        }
    )
    assert hybrid_days == "Hybrid · 3d Kraków"
    hybrid, _ = work_location_label(
        {"modality": "hybrid", "location_city": "Kraków"}
    )
    assert hybrid == "Hybrid · Kraków"
    onsite, _ = work_location_label(
        {"modality": "onsite", "location_city": "Copenhagen"}
    )
    assert onsite == "Onsite · Copenhagen"
    missing, _ = work_location_label(
        {"location_city": None, "location_country": "Ireland"}
    )
    assert missing == "Ireland"


def test_serialize_inbox_gross_month_and_pay_month(workspace):
    data, _env = workspace
    with Workspace.open(data) as ws:
        item = add_item(
            ws,
            company="Band Co",
            title="SRE",
            payload={
                "comp_quoted": {
                    "amount": 7500,
                    "amount_to": 9000,
                    "currency": "EUR",
                    "unit": "month",
                    "gross": False,
                    "kind": "band",
                },
                "engagement": "b2b",
                "modality": "remote",
                "location_city": "Warszawa",
                "location_country": "PL",
            },
        )
        row = serialize_inbox_item(ws, item)
    assert row["location"] == "Remote · PL"
    assert row["location_title"] == "Warszawa"
    assert row["gross_month"] == row["comp_derived"]["month"]
    assert row["comp_derived"]["month_to"] == 9000.0
    assert row["pay_month"]["line1"] == "€7.5–9.0k"
    assert row["pay_month"]["caption"] == "B2B · band"
    assert row["pay_month"]["kind"] == "band"
    assert "€" not in row["pay_month"]["compact"]
    with Workspace.open(data) as ws:
        blank = add_item(ws, company="None Co", title="Ops")
        empty = serialize_inbox_item(ws, blank)
    assert empty["pay_month"]["line1"] == "—"
    assert empty["pay_month"]["caption"] == "unknown"
    assert empty["gross_month"] is None
