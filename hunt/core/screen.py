"""Code knockouts. Listings become inbox items here, never applications."""

from __future__ import annotations

from typing import Any

from hunt.core.inbox import add_inbox_for_listing
from hunt.core.listings import Listing, listings_without_inbox
from hunt.core.workspace import Workspace


def evaluate_knockouts(
    ws: Workspace,
    *,
    title: str | None,
    payload: dict[str, Any],
) -> list[str]:
    rules = ws.knockout_rules()
    found: list[str] = []
    quoted = payload.get("comp_quoted")
    if not isinstance(quoted, dict) or quoted.get("amount") is None:
        found.append("pay_unknown")
    title_text = (title or "").lower()
    include = [str(t).lower() for t in (rules.get("title_include") or []) if t]
    if include and not any(token in title_text for token in include):
        found.append("title")
    modality = payload.get("modality")
    blocked_mod = {str(m) for m in (rules.get("modality_block") or []) if m}
    if modality and modality in blocked_mod:
        found.append("modality")
    allow = [str(e) for e in (rules.get("engagement_allow") or []) if e]
    engagement = payload.get("engagement")
    if allow and engagement and engagement not in allow:
        found.append("engagement")
    languages = payload.get("languages_required") or []
    if isinstance(languages, list):
        lang_text = " ".join(str(x) for x in languages).lower()
    else:
        lang_text = str(languages).lower()
    blob = f"{lang_text} {title_text}"
    for blocked in rules.get("languages_block") or []:
        token = str(blocked).lower()
        if token and token in blob:
            found.append("language")
            break
    # de-dupe, keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def screen_listing(ws: Workspace, listing: Listing) -> dict[str, Any]:
    knockouts = evaluate_knockouts(
        ws, title=listing.title, payload=listing.payload
    )
    drop_on = {str(x) for x in (ws.knockout_rules().get("drop_on") or []) if x}
    if drop_on and set(knockouts) & drop_on:
        return {"listing_id": listing.id, "dropped": True, "knockouts": knockouts}
    why_risk = ", ".join(knockouts) if knockouts else None
    why_keep = None if knockouts else "passed workspace knockouts"
    item = add_inbox_for_listing(
        ws,
        listing.id,
        why_keep=why_keep,
        why_risk=why_risk,
        knockouts=knockouts,
        commit=False,
    )
    return {
        "listing_id": listing.id,
        "dropped": False,
        "inbox_id": item.id,
        "knockouts": knockouts,
    }


def screen_inbox(ws: Workspace) -> dict[str, Any]:
    added = 0
    dropped = 0
    screened = 0
    for listing in listings_without_inbox(ws):
        screened += 1
        result = screen_listing(ws, listing)
        if result.get("dropped"):
            dropped += 1
        else:
            added += 1
    ws.conn.commit()
    return {"screened": screened, "inbox_added": added, "dropped": dropped}
