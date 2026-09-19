"""Layer 2 cheap structured triage. Title cards only. Never promote or apply."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from typing import Any

from hunt.agent.config import (
    agent_triage_model,
    agent_triage_section,
    api_key_for,
    require_local_triage_url,
    triage_auto_dismiss,
    triage_geo_hint,
    triage_keep_hint,
    triage_max_cards,
)
from hunt.agent.wake import maybe_wake_screener
from hunt.core.applications import derive_for_workspace
from hunt.core.errors import HuntError
from hunt.core.ids import now_iso
from hunt.core.inbox import InboxItem, dismiss, get_inbox_item, list_inbox, location_label
from hunt.core.jobs import Job, drop_on_codes
from hunt.core.pay import QuotedPay, below_workspace_floor, floor_month_for_workspace
from hunt.core.screen import is_boilerplate_why
from hunt.core.workspace import Workspace

CARD_TITLE_MAX = 80
CARD_COMPANY_MAX = 40
CARD_LOCATION_MAX = 40
BATCH_SIZE_DEFAULT = 8
REASON_MAX_DEFAULT = 80
POST_TIMEOUT_S = 45
TEMPERATURE = 0.0
MAX_CARDS_DEFAULT = 64
MAX_CARDS_PYTEST = 8

HASH_KEYS = (
    "title",
    "company",
    "location",
    "modality",
    "engagement",
    "experience_level",
    "source_id",
    "pay_unknown",
    "quoted",
)
ALLOWED_ACTIONS = frozenset({"dismiss", "keep", "unsure"})
ALLOWED_CODES = frozenset(
    {"junior", "pay_below_floor", "wrong_role", "wrong_seniority", "geo"}
)
PAY_REASON_RE = re.compile(r"salary|comp|floor|pay|netto", re.I)

TRIAGE_SYSTEM_PROMPT = """You classify Hunt inbox cards for a job search. Output JSON only.

Return {"results":[{"id":"...","action":"dismiss|keep|unsure","codes":["..."],"reason":"..."}]}.
Every input card id must appear at most once. Do not invent ids. Do not omit ids if you can classify them; if unsure, action=unsure.

The user JSON includes keep_hint and geo_hint from this workspace. Treat them as policy. If keep_hint is empty, prefer unsure unless the title is an obvious mismatch. If geo_hint is empty, do not use the geo code.

Actions:
- dismiss: obvious mismatch from the provided fields only.
- keep: plausible match for keep_hint.
- unsure: missing fields, conflicting signals, empty keep_hint, or anything that needs a human.

Allowed codes (subset, may be empty on keep/unsure):
- junior: title or experience_level says intern/junior/trainee (not mid/senior).
- pay_below_floor: only if the card has below_floor=true (Hunt already compared FX gross month or net to the workspace floor). Never if pay_unknown=true. Never convert quoted currency yourself. Never invent a net or a salary.
- wrong_role: title is a different job than keep_hint describes. If keep_hint is empty, only use this for an obvious mismatch (helpdesk, Microsoft 365 admin, unrelated function).
- wrong_seniority: experience_level or title is a seniority mismatch — not a pay judgment.
- geo: location field is clearly outside geo_hint as written on the card. Do not guess geo from company name.

Hard rules:
- Never promote. Never apply. Never send mail.
- Cite only provided fields in reason (<= 80 characters).
- Do not use wrong_role or geo as a proxy for compensation. If pay_unknown, you must not dismiss for pay; use unsure if pay is the only issue.
- If below_floor is false or null and pay_unknown is false, do not set pay_below_floor.
- If you cannot tell, action=unsure.
"""


def trunc(value: Any, n: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return text
    return text if len(text) <= n else text[:n]


def format_quoted(quoted: QuotedPay) -> str:
    amount = quoted.amount
    shown = int(amount) if float(amount).is_integer() else amount
    return f"{shown} {quoted.currency}/{quoted.unit}"


def _quoted_from_payload(payload: dict[str, Any]) -> QuotedPay | None:
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


def listing_card(
    item: InboxItem, *, source_id: str | None, ws: Workspace, derived=None
) -> dict[str, Any]:
    quoted = _quoted_from_payload(item.payload)
    if derived is None:
        derived = derive_for_workspace(
            ws,
            quoted,
            item.payload.get("tax_home_for_net"),
            country=item.payload.get("location_country"),
            engagement=item.payload.get("engagement"),
        )
    below = below_workspace_floor(ws, derived)
    return {
        "id": item.id,
        "title": trunc(item.title, CARD_TITLE_MAX),
        "company": trunc(item.company, CARD_COMPANY_MAX),
        "location": trunc(location_label(item.payload), CARD_LOCATION_MAX),
        "modality": item.payload.get("modality"),
        "engagement": item.payload.get("engagement"),
        "experience_level": item.payload.get("experience_level"),
        "source_id": source_id,
        "pay_unknown": quoted is None,
        "quoted": format_quoted(quoted) if quoted else None,
        "gross_month": derived.month if derived is not None else None,
        "display_currency": ws.display_currency,
        "clears_floor": derived.clears_floor if derived is not None else None,
        "below_floor": below,
    }


def input_hash(card: dict[str, Any]) -> str:
    payload = {k: card.get(k) for k in HASH_KEYS}
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _card_for(ws: Workspace, item: InboxItem) -> dict[str, Any]:
    return listing_card(item, source_id=item.source_id, ws=ws)


def is_protected(item: InboxItem) -> bool:
    triage = item.triage or {}
    if triage.get("restored"):
        return True
    if not is_boilerplate_why(item.why_keep, item.knockouts):
        return True
    if not is_boilerplate_why(item.why_risk, item.knockouts):
        return True
    return False


def _needs_model_card(
    ws: Workspace, item: InboxItem, *, force: bool, drop_on: set[str]
) -> bool:
    if item.status != "pending":
        return False
    if set(item.knockouts) & drop_on:
        return False
    if is_protected(item) and not force:
        return False
    triage = item.triage or {}
    if force:
        return True
    if not triage:
        return True
    if triage.get("skipped_human_note"):
        return False
    return triage.get("input_hash") != input_hash(_card_for(ws, item))


def count_untriaged_pending(ws: Workspace) -> int:
    drop_on = set(drop_on_codes(ws))
    pending = list_inbox(ws, status="pending")
    return sum(
        1
        for item in pending
        if _needs_model_card(ws, item, force=False, drop_on=drop_on)
    )


def _select_pass_a(
    ws: Workspace, *, force: bool, limit: int
) -> list[InboxItem]:
    drop_on = set(drop_on_codes(ws))
    pending = list_inbox(ws, status="pending")
    pending.sort(key=lambda item: (item.created_at or "", item.id))
    selected: list[InboxItem] = []
    for item in pending:
        if not _needs_model_card(ws, item, force=force, drop_on=drop_on):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _select_pass_b(ws: Workspace) -> list[InboxItem]:
    pending = list_inbox(ws, status="pending")
    out: list[InboxItem] = []
    for item in pending:
        triage = item.triage or {}
        if triage.get("skipped_human_note"):
            continue
        if triage.get("model") is not None:
            continue
        if triage.get("restored"):
            continue
        if not is_protected(item):
            continue
        out.append(item)
    return out


def _reason_max(ws: Workspace) -> int:
    raw = agent_triage_section(ws).get("reason_max_chars", REASON_MAX_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return REASON_MAX_DEFAULT
    return n if n > 0 else REASON_MAX_DEFAULT


def _batch_size(ws: Workspace) -> int:
    raw = agent_triage_section(ws).get("batch_size", BATCH_SIZE_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = BATCH_SIZE_DEFAULT
    return max(1, min(12, n))


def _job_max_cards(ws: Workspace, job: Job) -> int:
    cap = triage_max_cards(ws)
    raw = (job.payload or {}).get("max_cards")
    if raw is None:
        return cap
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return cap
    if n < 1:
        n = 1
    return min(n, cap)


def chat_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8080/v1"
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def chat_completions(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str]:
    """POST JSON. Tests monkeypatch this; pytest must not open a socket."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            return status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        if exc.fp is not None:
            body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body


def _strip_fences(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def parse_results(content: str) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(_strip_fences(content))
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("results"), list):
        rows = data["results"]
    else:
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def validate_outcome(
    raw: dict[str, Any], card: dict[str, Any], *, reason_max: int
) -> dict[str, Any] | None:
    action = raw.get("action")
    if action not in ALLOWED_ACTIONS:
        return None
    incoming = raw.get("codes") if isinstance(raw.get("codes"), list) else []
    codes = [str(code) for code in incoming if str(code) in ALLOWED_CODES]
    reason = str(raw.get("reason") or "")[:reason_max]
    below = bool(card.get("below_floor"))
    pay_unknown = bool(card.get("pay_unknown"))
    if "pay_below_floor" in codes and (not below or pay_unknown):
        codes = [code for code in codes if code != "pay_below_floor"]
    if action == "dismiss" and not codes:
        action = "unsure"
    if pay_unknown and action == "dismiss":
        if not codes or PAY_REASON_RE.search(reason):
            action = "unsure"
    ident = raw.get("id")
    if not ident:
        return None
    return {
        "id": str(ident),
        "action": action,
        "codes": codes,
        "reason": reason,
    }


def _union_codes(existing: list[str], extra: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in list(existing) + list(extra):
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def apply_outcome(
    ws: Workspace,
    item: InboxItem,
    outcome: dict[str, Any],
    *,
    model_name: str | None,
    card: dict[str, Any],
    auto_dismiss: bool,
) -> str:
    """Write triage_json / knockouts. Returns the stored action. Never promotes."""
    now = now_iso()
    protected = is_protected(item)
    action = outcome["action"]
    if protected:
        action = "keep"
    codes = list(outcome["codes"])
    knockouts = _union_codes(item.knockouts, codes + ["triaged"])
    prior = dict(item.triage or {})
    triage: dict[str, Any] = {
        "action": action,
        "codes": codes,
        "reason": outcome["reason"],
        "model": model_name,
        "at": now,
        "input_hash": input_hash(card),
    }
    if prior.get("restored"):
        triage["restored"] = True
        if prior.get("restored_at"):
            triage["restored_at"] = prior["restored_at"]
    why_risk = item.why_risk
    if is_boilerplate_why(why_risk, item.knockouts) and not prior.get("reason"):
        joined = ", ".join(codes)
        snippet = (outcome["reason"] or "").strip()
        if joined and snippet:
            why_risk = f"{joined} {snippet}"[: _reason_max(ws)]
        else:
            why_risk = (joined or snippet or why_risk)
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET knockouts_json = ?, triage_json = ?, why_risk = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(knockouts), json.dumps(triage), why_risk, now, item.id),
    )
    ws.conn.commit()
    if (
        not protected
        and action == "dismiss"
        and auto_dismiss
    ):
        dismiss(ws, item.id, commit=True)
    return action


def stamp_human_note(ws: Workspace, item: InboxItem) -> None:
    now = now_iso()
    card = _card_for(ws, item)
    triage = dict(item.triage or {})
    triage.update(
        {
            "action": "keep",
            "codes": [],
            "reason": "human note; skipped cheap triage",
            "model": None,
            "at": now,
            "input_hash": input_hash(card),
            "skipped_human_note": True,
        }
    )
    ws.conn.execute(
        """
        UPDATE inbox_items
        SET triage_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(triage), now, item.id),
    )
    ws.conn.commit()


def _user_payload(
    ws: Workspace,
    cards: list[dict[str, Any]],
    *,
    keep_hint: str,
    geo_hint: str,
) -> dict[str, Any]:
    floor = ws.comp_floor if isinstance(ws.comp_floor, dict) else {}
    if not floor:
        amount = floor_month_for_workspace(ws)
        if amount is not None:
            floor = {
                "amount": amount,
                "currency": ws.display_currency,
                "unit": "month",
            }
    return {
        "keep_hint": keep_hint,
        "geo_hint": geo_hint,
        "floor": floor,
        "cards": cards,
    }


def _headers(ws: Workspace, model_cfg: dict[str, str]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    env_name = (model_cfg.get("api_key_env") or "").strip()
    if env_name:
        key = api_key_for(ws, model_cfg)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _request_body(
    model_cfg: dict[str, str],
    user_json: dict[str, Any],
    *,
    response_format: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model_cfg["model"],
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_json, ensure_ascii=False),
            },
        ],
    }
    if response_format:
        body["response_format"] = {"type": "json_object"}
    return body


def _post_batch(
    ws: Workspace,
    url: str,
    model_cfg: dict[str, str],
    user_json: dict[str, Any],
    *,
    timeout: float = POST_TIMEOUT_S,
) -> tuple[str, Any]:
    headers = _headers(ws, model_cfg)
    body = _request_body(model_cfg, user_json, response_format=True)
    try:
        status, text = chat_completions(
            url, body, headers=headers, timeout=timeout
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        try:
            status, text = chat_completions(
                url, body, headers=headers, timeout=timeout
            )
        except (TimeoutError, urllib.error.URLError, OSError):
            return "timeout", str(exc)
    if status in {401, 403, 404}:
        return "auth", status
    if status == 400:
        retry_body = _request_body(model_cfg, user_json, response_format=False)
        try:
            status, text = chat_completions(
                url, retry_body, headers=headers, timeout=timeout
            )
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return "timeout", str(exc)
        if status >= 400:
            return "http", status
        parsed = parse_results(_message_content(text))
        if parsed is None:
            return "invalid", status
        return "ok", parsed
    if status >= 500:
        try:
            status, text = chat_completions(
                url, body, headers=headers, timeout=timeout
            )
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return "timeout", str(exc)
        if status >= 500:
            return "http5", status
    if status >= 400:
        return "http", status
    parsed = parse_results(_message_content(text))
    if parsed is None:
        return "invalid", status
    return "ok", parsed


def _message_content(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    if isinstance(payload.get("content"), str):
        return payload["content"]
    return text


def _commit_parsed(
    ws: Workspace,
    parsed: list[dict[str, Any]],
    batch: list[InboxItem],
    cards_by_id: dict[str, dict[str, Any]],
    *,
    model_name: str,
    auto_dismiss: bool,
    reason_max: int,
    counts: dict[str, int],
) -> set[str]:
    batch_ids = {item.id for item in batch}
    extra = 0
    seen: set[str] = set()
    committed: set[str] = set()
    for raw in parsed:
        ident = str(raw.get("id") or "")
        if ident not in batch_ids:
            extra += 1
            continue
        if ident in seen:
            continue
        seen.add(ident)
        card = cards_by_id[ident]
        outcome = validate_outcome(raw, card, reason_max=reason_max)
        if outcome is None:
            counts["invalid"] += 1
            continue
        item = get_inbox_item(ws, ident)
        action = apply_outcome(
            ws,
            item,
            outcome,
            model_name=model_name,
            card=card,
            auto_dismiss=auto_dismiss,
        )
        committed.add(ident)
        if action == "dismiss":
            counts["dismissed"] += 1
        elif action == "keep":
            counts["kept"] += 1
        else:
            counts["unsure"] += 1
    extra_rate = extra / max(1, len(batch))
    if extra_rate > 0.25 and not committed:
        return set()
    return committed


def run_triage_inbox(ws: Workspace, job: Job) -> dict[str, Any]:
    """Classify pending cards. Never promotes. Never self-enqueues."""
    model_cfg = agent_triage_model(ws)
    require_local_triage_url(model_cfg["base_url"])
    url = chat_url(model_cfg["base_url"])
    force = bool((job.payload or {}).get("force"))
    stamp_notes = bool((job.payload or {}).get("stamp_human_notes"))
    limit = _job_max_cards(ws, job)
    auto_dismiss = triage_auto_dismiss(ws)
    reason_max = _reason_max(ws)
    keep_hint = triage_keep_hint(ws)
    geo_hint = triage_geo_hint(ws)
    selected_items = _select_pass_a(ws, force=force, limit=limit)
    counts = {
        "dismissed": 0,
        "kept": 0,
        "unsure": 0,
        "invalid": 0,
        "skipped_human_note": 0,
    }
    selected_n = len(selected_items)
    remaining_ids = [item.id for item in selected_items]
    batch_size = _batch_size(ws)
    failed_reason: str | None = None
    any_committed = False
    shrink_used = False

    def _refresh(ids: list[str]) -> list[InboxItem]:
        return [get_inbox_item(ws, ident) for ident in ids]

    while remaining_ids:
        chunk_ids = remaining_ids[:batch_size]
        chunk = _refresh(chunk_ids)
        cards = [_card_for(ws, item) for item in chunk]
        cards_by_id = {card["id"]: card for card in cards}
        user_json = _user_payload(
            ws, cards, keep_hint=keep_hint, geo_hint=geo_hint
        )
        kind, payload = _post_batch(ws, url, model_cfg, user_json)
        if kind == "ok":
            committed = _commit_parsed(
                ws,
                payload,
                chunk,
                cards_by_id,
                model_name=model_cfg["model"],
                auto_dismiss=auto_dismiss,
                reason_max=reason_max,
                counts=counts,
            )
            if committed:
                any_committed = True
                remaining_ids = [i for i in remaining_ids if i not in committed]
                uncommitted = [i for i in chunk_ids if i not in committed]
                if uncommitted:
                    remaining_ids = uncommitted + [
                        i for i in remaining_ids if i not in uncommitted
                    ]
                    if not shrink_used and len(uncommitted) == len(chunk_ids):
                        shrink_used = True
                        batch_size = max(1, batch_size // 2)
                    elif uncommitted and batch_size > 1:
                        batch_size = max(1, batch_size // 2)
                    continue
                continue
            # extra-id or unusable parse treated as shrink
            kind = "invalid"
        if kind in {"timeout", "auth"}:
            failed_reason = (
                f"triage HTTP {payload}" if kind == "auth" else f"triage timeout at {url}"
            )
            break
        if kind in {"http", "http5", "invalid"}:
            if kind == "http" and payload in {401, 403, 404}:
                failed_reason = f"triage HTTP {payload} at {url}"
                break
            if not shrink_used:
                shrink_used = True
                batch_size = max(1, batch_size // 2)
                if kind == "http5":
                    failed_reason = f"http_{payload}"
                continue
            if kind == "http5":
                failed_reason = f"http_{payload}"
            else:
                failed_reason = f"triage HTTP {payload}" if kind == "http" else "invalid_response"
            remaining_ids = remaining_ids[len(chunk_ids) :]
            counts["invalid"] += len(chunk_ids)
            break
        remaining_ids = remaining_ids[len(chunk_ids) :]

    if stamp_notes:
        for item in _select_pass_b(ws):
            stamp_human_note(ws, item)
            counts["skipped_human_note"] += 1

    committed_actions = counts["dismissed"] + counts["kept"] + counts["unsure"]
    invalid = max(0, selected_n - committed_actions)
    counts["invalid"] = invalid
    remaining = count_untriaged_pending(ws)
    survivors = counts["kept"] + counts["unsure"]
    result: dict[str, Any] = {
        "selected": selected_n,
        "dismissed": counts["dismissed"],
        "kept": counts["kept"],
        "unsure": counts["unsure"],
        "invalid": counts["invalid"],
        "skipped_human_note": counts["skipped_human_note"],
        "remaining_untriaged": remaining,
        "capped": remaining > 0,
        "outcomes": committed_actions + counts["invalid"],
        "never_apply": True,
        "never_send_mail": True,
        "never_promote": True,
    }
    if failed_reason:
        result["failed_reason"] = failed_reason
        if not any_committed:
            raise HuntError(failed_reason)
    result["screener"] = maybe_wake_screener(ws, survivors=survivors)
    return result
