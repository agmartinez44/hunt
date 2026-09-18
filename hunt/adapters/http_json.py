"""Generic HTTP JSON source. GET only. JustJoin is a named config profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

from hunt.adapters import RawListing
from hunt.core.errors import HuntError, ValidationError

JUSTJOIN_URL = "https://justjoin.it/api/candidate-api/offers"
HUNT_UA = "Hunt/0.1 (source-poll; GET only; never-apply)"

PROFILES: dict[str, dict[str, Any]] = {
    "justjoin": {
        "url": JUSTJOIN_URL,
        "query": {"itemsCount": "50"},
        "items_path": "data",
        "mapper": "justjoin",
        "paginate": {
            "param": "from",
            "page_size": 50,
            "cursor_path": "meta.next.cursor",
            "total_path": "meta.totalItems",
            "max_pages": 10,
        },
    }
}


def _dotted(data: Any, path: str | None) -> Any:
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _get_json(url: str, headers: dict[str, str], timeout: int = 30) -> Any:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return json.loads(Path(parsed.path).read_text(encoding="utf-8"))
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError(f"http_json url must be http(s) or file, got {parsed.scheme!r}")
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _with_query(url: str, extra: dict[str, Any]) -> str:
    parsed = urlparse(url)
    current = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in extra.items():
        if value is None:
            continue
        current[str(key)] = str(value)
    return urlunparse(parsed._replace(query=urlencode(current)))


def _justjoin_listing(item: dict[str, Any]) -> RawListing | None:
    title = str(item.get("title") or "").strip()
    company = str(item.get("companyName") or item.get("company") or "").strip()
    if not title or not company:
        return None
    slug = str(item.get("slug") or "").strip()
    guid = str(item.get("guid") or item.get("id") or slug).strip()
    url = f"https://justjoin.it/job-offer/{slug}" if slug else None
    workplace = str(item.get("workplaceType") or "").lower()
    modality = {"remote": "remote", "hybrid": "hybrid", "office": "onsite"}.get(
        workplace
    )
    quoted, engagement = _justjoin_pay(item.get("employmentTypes") or [])
    city = item.get("city")
    country = item.get("countryCode") or item.get("country")
    payload: dict[str, Any] = {
        "company": company,
        "title_posted": title,
        "source": "http_json",
        "url": url,
        "location_city": city,
        "location_country": country,
        "modality": modality,
        "engagement": engagement,
        "experience_level": item.get("experienceLevel"),
    }
    if quoted:
        payload["comp_quoted"] = quoted
    return RawListing(
        external_id=guid or f"{company}:{title}",
        title=title,
        company=company,
        url=url,
        payload=payload,
    )


def _justjoin_pay(employment_types: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(employment_types, list) or not employment_types:
        return None, None
    ranked = sorted(
        [e for e in employment_types if isinstance(e, dict)],
        key=lambda e: 0 if str(e.get("type") or "").lower() == "b2b" else 1,
    )
    if not ranked:
        return None, None
    row = ranked[0]
    raw_type = str(row.get("type") or "").lower()
    engagement = {"b2b": "b2b", "permanent": "fte", "uop": "uop"}.get(
        raw_type, "unknown" if raw_type else None
    )
    unit = str(row.get("unit") or "month").lower()
    if unit not in {"hour", "day", "month", "year"}:
        unit = "month"
    amount = row.get("fromPerUnit")
    if amount is None:
        amount = row.get("from")
    currency = str(row.get("currency") or "").upper() or None
    quoted = None
    if amount is not None and currency:
        try:
            quoted = {"amount": float(amount), "currency": currency, "unit": unit}
        except (TypeError, ValueError):
            quoted = None
    return quoted, engagement


def _generic_listing(item: Any, mapping: dict[str, Any]) -> RawListing | None:
    if not isinstance(item, dict):
        return None
    title = str(_dotted(item, mapping.get("title") or "title") or "").strip()
    company = str(_dotted(item, mapping.get("company") or "company") or "").strip()
    if not title or not company:
        return None
    external = _dotted(item, mapping.get("external_id") or "id")
    url = _dotted(item, mapping.get("url") or "url")
    template = mapping.get("url_template")
    if template and not url:
        slug = _dotted(item, mapping.get("slug") or "slug") or ""
        url = str(template).format(slug=slug, id=external or "")
    payload = dict(item)
    payload.setdefault("company", company)
    payload.setdefault("title_posted", title)
    if url:
        payload.setdefault("url", str(url))
    return RawListing(
        external_id=str(external or f"{company}:{title}"),
        title=title,
        company=company,
        url=str(url) if url else None,
        payload=payload,
    )


def _items(payload: Any, items_path: str | None) -> list[Any]:
    data = _dotted(payload, items_path) if items_path else payload
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _resolve_config(config: dict[str, Any]) -> dict[str, Any]:
    profile = config.get("profile")
    base = dict(PROFILES.get(str(profile)) or {}) if profile else {}
    merged = {**base, **{k: v for k, v in config.items() if k != "profile"}}
    if isinstance(base.get("query"), dict) and isinstance(config.get("query"), dict):
        merged["query"] = {**base["query"], **config["query"]}
    if isinstance(base.get("paginate"), dict) and isinstance(config.get("paginate"), dict):
        merged["paginate"] = {**base["paginate"], **config["paginate"]}
    return merged


def poll_http_json(
    config: dict[str, Any],
    *,
    workspace_root: Path | None = None,
    get_json: Callable[..., Any] | None = None,
) -> list[RawListing]:
    cfg = _resolve_config(config)
    method = str(cfg.get("method") or "GET").upper()
    if method != "GET":
        raise ValidationError("http_json adapters are GET-only; they never submit forms")
    fetch = get_json or _get_json
    headers = {"Accept": "application/json", "User-Agent": HUNT_UA}
    extra_headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    headers.update({str(k): str(v) for k, v in extra_headers.items()})

    path = cfg.get("path")
    if path:
        file_path = Path(str(path))
        if not file_path.is_absolute():
            if workspace_root is None:
                raise ValidationError("http_json path needs a workspace")
            file_path = workspace_root / file_path
        if not file_path.is_file():
            raise HuntError(f"http_json path not found: {file_path}")
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return _map_items(payload, cfg)

    url = cfg.get("url")
    if not url:
        raise ValidationError("http_json needs url or path")
    query = cfg.get("query") if isinstance(cfg.get("query"), dict) else {}
    paginate = cfg.get("paginate") if isinstance(cfg.get("paginate"), dict) else {}
    max_pages = int(paginate.get("max_pages") or 1)
    page_size = int(paginate.get("page_size") or query.get("itemsCount") or 50)
    param = paginate.get("param")
    seen: set[str] = set()
    out: list[RawListing] = []
    cursor: Any = query.get(param) if param else 0
    pages = 0
    while pages < max_pages:
        extra = dict(query)
        if param:
            extra[param] = cursor if cursor is not None else pages * page_size
        page_url = _with_query(str(url), extra)
        payload = fetch(page_url, headers)
        batch = _map_items(payload, cfg)
        if not batch:
            break
        new = 0
        for listing in batch:
            if listing.external_id in seen:
                continue
            seen.add(listing.external_id)
            out.append(listing)
            new += 1
        pages += 1
        if new == 0:
            break
        nxt = _dotted(payload, paginate.get("cursor_path")) if paginate.get("cursor_path") else None
        total = _dotted(payload, paginate.get("total_path")) if paginate.get("total_path") else None
        if nxt is not None:
            cursor = nxt
        elif param:
            cursor = int(cursor or 0) + len(batch)
        else:
            break
        if total is not None:
            try:
                if int(cursor or 0) >= int(total):
                    break
            except (TypeError, ValueError):
                pass
        if len(batch) < page_size:
            break
    return out


def _map_items(payload: Any, cfg: dict[str, Any]) -> list[RawListing]:
    mapper = cfg.get("mapper")
    mapping = cfg.get("map") if isinstance(cfg.get("map"), dict) else {}
    items = _items(payload, cfg.get("items_path"))
    out: list[RawListing] = []
    for item in items:
        if mapper == "justjoin":
            listing = _justjoin_listing(item if isinstance(item, dict) else {})
        else:
            listing = _generic_listing(item, mapping)
        if listing:
            out.append(listing)
    return out
