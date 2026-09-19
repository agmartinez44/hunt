"""Code knockouts. Listings become inbox items here, never applications."""

from __future__ import annotations

from typing import Any

import json

from hunt.adapters.linkedin_alert import parse_subject
from hunt.core.applications import derive_for_workspace
from hunt.core.inbox import add_inbox_for_listing, dismiss, refresh_inbox_for_listing
from hunt.core.listings import (
    Listing,
    apply_stated_engagement,
    backfill_engagement,
    get_listing,
    list_listings,
)
from hunt.core.pay import QuotedPay, below_workspace_floor
from hunt.core.workspace import Workspace

_MAILBOX_COMPANIES = {"linkedin", "job alert", "imap", "mail"}
PASSED_WORKSPACE_KNOCKOUTS = "passed workspace knockouts"
LAYER1_CODES = frozenset(
    {
        "pay_unknown",
        "title",
        "title_exclude",
        "modality",
        "engagement",
        "language",
        "experience",
        "pay_below_floor",
    }
)
LAYER2_CODES = frozenset(
    {
        "junior",
        "pay_below_floor",
        "wrong_role",
        "wrong_seniority",
        "geo",
        "triaged",
    }
)
HISTORICAL_AGENT_CODES = frozenset(
    {"below_floor", "duplicate", "recruiter", "current_employer"}
)
_SCREEN_KNOCKOUT_CODES = LAYER1_CODES | LAYER2_CODES | HISTORICAL_AGENT_CODES


def _enrich_listing_from_subject(ws: Workspace, listing: Listing) -> Listing:
    """If company is still the mailbox label, parse ``X is hiring Y`` from title."""
    parsed = parse_subject(listing.title or "")
    company = parsed.get("company")
    title = parsed.get("title")
    if not company or not title:
        return listing
    current = (listing.company or "").strip().lower()
    if current and current not in _MAILBOX_COMPANIES:
        return listing
    payload = dict(listing.payload)
    payload["company"] = company
    payload["title_posted"] = title
    if parsed.get("comp_quoted") and not payload.get("comp_quoted"):
        payload["comp_quoted"] = parsed["comp_quoted"]
    if parsed.get("engagement") and not payload.get("engagement"):
        payload["engagement"] = parsed["engagement"]
    if parsed.get("modality") and not payload.get("modality"):
        payload["modality"] = parsed["modality"]
    ws.conn.execute(
        """
        UPDATE listings
        SET title = ?, company = ?, payload_json = ?
        WHERE id = ?
        """,
        (title, company, json.dumps(payload), listing.id),
    )
    return get_listing(ws, listing.id) or listing


def _quoted_pay(payload: dict[str, Any]) -> QuotedPay | None:
    quoted = payload.get("comp_quoted")
    if not isinstance(quoted, dict) or quoted.get("amount") is None:
        return None
    try:
        return QuotedPay(
            amount=float(quoted["amount"]),
            currency=str(quoted.get("currency") or "EUR"),
            unit=str(quoted.get("unit") or "month"),
        )
    except (TypeError, ValueError):
        return None


def evaluate_knockouts(
    ws: Workspace,
    *,
    title: str | None,
    payload: dict[str, Any],
) -> list[str]:
    rules = ws.knockout_rules()
    found: list[str] = []
    quoted = _quoted_pay(payload)
    if quoted is None:
        found.append("pay_unknown")
    title_text = (title or "").lower()
    include = [str(t).lower() for t in (rules.get("title_include") or []) if t]
    if include and not any(token in title_text for token in include):
        found.append("title")
    exclude = [str(t).lower() for t in (rules.get("title_exclude") or []) if t]
    if exclude and any(token in title_text for token in exclude):
        found.append("title_exclude")
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
    blocked_exp = {
        str(x).strip().lower() for x in (rules.get("experience_block") or []) if x
    }
    level = payload.get("experience_level")
    if level is not None and str(level).strip() and str(level).strip().lower() in blocked_exp:
        found.append("experience")
    derived = derive_for_workspace(
        ws,
        quoted,
        payload.get("tax_home_for_net"),
        country=payload.get("location_country"),
        engagement=payload.get("engagement"),
    )
    if below_workspace_floor(ws, derived):
        found.append("pay_below_floor")
    # de-dupe, keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _knockout_code_set(*groups: list[str] | None) -> set[str]:
    codes = set(_SCREEN_KNOCKOUT_CODES)
    for group in groups:
        if group:
            codes.update(str(item) for item in group)
    return codes


def is_boilerplate_why(
    value: str | None, *knockout_groups: list[str] | None
) -> bool:
    """True when *value* is empty or machine knockout boilerplate.

    Agent/human notes (company, role, geo, net) are not boilerplate.
    Joined knockout codes only (``pay_unknown``, ``pay_unknown, title``)
    are, so a later refresh can replace them.
    """
    text = (value or "").strip()
    if not text or text == PASSED_WORKSPACE_KNOCKOUTS:
        return True
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return True
    known = _knockout_code_set(*knockout_groups)
    return all(part in known for part in parts)


def _why_for_knockouts(knockouts: list[str]) -> tuple[str | None, str | None]:
    why_risk = ", ".join(knockouts) if knockouts else None
    why_keep = None if knockouts else PASSED_WORKSPACE_KNOCKOUTS
    return why_keep, why_risk


def _merge_why(
    existing_keep: str | None,
    existing_risk: str | None,
    new_keep: str | None,
    new_risk: str | None,
    existing_knockouts: list[str] | None,
    knockouts: list[str] | None,
) -> tuple[str | None, str | None]:
    keep = (
        new_keep
        if is_boilerplate_why(existing_keep, existing_knockouts, knockouts)
        else existing_keep
    )
    risk = (
        new_risk
        if is_boilerplate_why(existing_risk, existing_knockouts, knockouts)
        else existing_risk
    )
    return keep, risk


def _existing_knockouts(row: Any) -> list[str]:
    parsed = json.loads(row["knockouts_json"] or "[]")
    return parsed if isinstance(parsed, list) else []


def _parse_triage(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _union_knockouts(layer1: list[str], existing: list[str]) -> list[str]:
    preserved = [code for code in existing if code not in LAYER1_CODES]
    seen: set[str] = set()
    ordered: list[str] = []
    for item in list(layer1) + preserved:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _should_layer1_dismiss(
    *,
    drop_on: set[str],
    layer1: list[str],
    why_keep: str | None,
    why_risk: str | None,
    existing_knockouts: list[str],
    knockouts: list[str],
    triage: dict[str, Any],
) -> bool:
    if not (drop_on and set(layer1) & drop_on):
        return False
    if triage.get("restored"):
        return False
    if triage.get("action") in {"keep", "unsure"}:
        return False
    if triage.get("reason"):
        return False
    if not is_boilerplate_why(why_keep, existing_knockouts, knockouts):
        return False
    if not is_boilerplate_why(why_risk, existing_knockouts, knockouts):
        return False
    return True


def screen_listing(ws: Workspace, listing: Listing) -> dict[str, Any]:
    listing = _enrich_listing_from_subject(ws, listing)
    listing = apply_stated_engagement(ws, listing)
    layer1 = evaluate_knockouts(ws, title=listing.title, payload=listing.payload)
    drop_on = {str(x) for x in (ws.knockout_rules().get("drop_on") or []) if x}
    existing = ws.conn.execute(
        """
        SELECT id, status, why_keep, why_risk, knockouts_json, triage_json
        FROM inbox_items WHERE listing_id = ?
        """,
        (listing.id,),
    ).fetchone()
    if drop_on and set(layer1) & drop_on and existing is None:
        return {"listing_id": listing.id, "dropped": True, "knockouts": layer1}
    why_keep, why_risk = _why_for_knockouts(layer1)
    if existing:
        if existing["status"] == "pending":
            existing_codes = _existing_knockouts(existing)
            knockouts = _union_knockouts(layer1, existing_codes)
            triage = _parse_triage(existing["triage_json"])
            will_dismiss = _should_layer1_dismiss(
                drop_on=drop_on,
                layer1=layer1,
                why_keep=existing["why_keep"],
                why_risk=existing["why_risk"],
                existing_knockouts=existing_codes,
                knockouts=knockouts,
                triage=triage,
            )
            if will_dismiss:
                why_keep, why_risk = _why_for_knockouts(knockouts)
            elif triage.get("reason"):
                why_keep, why_risk = existing["why_keep"], existing["why_risk"]
            else:
                why_keep, why_risk = _merge_why(
                    existing["why_keep"],
                    existing["why_risk"],
                    *_why_for_knockouts(knockouts),
                    existing_codes,
                    knockouts,
                )
            item = refresh_inbox_for_listing(
                ws,
                listing.id,
                why_keep=why_keep,
                why_risk=why_risk,
                knockouts=knockouts,
                commit=False,
            )
            if will_dismiss:
                dismiss(ws, item.id, commit=False)
                return {
                    "listing_id": listing.id,
                    "dropped": False,
                    "dismissed": True,
                    "inbox_id": item.id,
                    "knockouts": knockouts,
                }
            return {
                "listing_id": listing.id,
                "dropped": False,
                "refreshed": True,
                "inbox_id": item.id,
                "knockouts": knockouts,
            }
        return {
            "listing_id": listing.id,
            "dropped": False,
            "skipped": True,
            "inbox_id": existing["id"],
            "knockouts": layer1,
        }
    item = add_inbox_for_listing(
        ws,
        listing.id,
        why_keep=why_keep,
        why_risk=why_risk,
        knockouts=layer1,
        commit=False,
    )
    return {
        "listing_id": listing.id,
        "dropped": False,
        "inbox_id": item.id,
        "knockouts": layer1,
    }


def screen_inbox(ws: Workspace) -> dict[str, Any]:
    added = 0
    dropped = 0
    screened = 0
    refreshed = 0
    dismissed = 0
    backfill_engagement(ws, commit=False)
    for listing in list_listings(ws):
        screened += 1
        result = screen_listing(ws, listing)
        if result.get("dropped"):
            dropped += 1
        elif result.get("dismissed"):
            dismissed += 1
        elif result.get("refreshed"):
            refreshed += 1
        elif result.get("skipped"):
            continue
        else:
            added += 1
    ws.conn.commit()
    return {
        "screened": screened,
        "inbox_added": added,
        "dropped": dropped,
        "refreshed": refreshed,
        "dismissed": dismissed,
    }
