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
    serialize_inbox_list,
    sort_serialized_inbox,
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
    assert resolve_inbox_sort("net_month", None) == ("net_month", "desc")
    assert resolve_inbox_sort("created_at", None) == ("created_at", "desc")
    assert resolve_inbox_sort("company", "desc") == ("company", "desc")
    with pytest.raises(ValidationError, match="inbox sort"):
        resolve_inbox_sort("nope", None)
    with pytest.raises(ValidationError, match="inbox order"):
        resolve_inbox_sort("company", "sideways")


def test_sort_serialized_inbox_missing_net_last():
    rows = [
        {"company": "A", "net_month": 100, "created_at": "2026-01-01T00:00:03Z"},
        {"company": "B", "net_month": None, "created_at": "2026-01-01T00:00:02Z"},
        {"company": "C", "net_month": 50, "created_at": "2026-01-01T00:00:01Z"},
        {"company": "D", "net_month": None, "created_at": "2026-01-01T00:00:00Z"},
    ]
    desc = sort_serialized_inbox(rows, sort="net_month")
    assert [r["company"] for r in desc] == ["A", "C", "B", "D"]
    asc = sort_serialized_inbox(rows, sort="net_month", order="asc")
    assert [r["company"] for r in asc] == ["C", "A", "B", "D"]
    empty = sort_serialized_inbox(
        [{"company": "E", "net_month": "", "created_at": "x"}],
        sort="net_month",
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
            "net_month",
        )
