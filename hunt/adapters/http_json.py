"""Generic HTTP JSON source. GET only. Named profiles: JustJoin, Remotive, Landing.jobs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

from hunt.adapters import RawListing
from hunt.core.errors import HuntError, ValidationError

JUSTJOIN_URL = "https://justjoin.it/api/candidate-api/offers"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
LANDING_JOBS_URL = "https://landing.jobs/api/v1/offers"
HUNT_UA = "Hunt/0.1 (source-poll; GET only; never-apply)"

# Remotive location tokens for EU / international freelance hiring.
REMOTIVE_EU_TOKENS = (
    "europe",
    "european",
    "eea",
    "emea",
    "eu",
    "poland",
    "polska",
    "worldwide",
    "anywhere",
)

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
    },
    "remotive": {
        "url": REMOTIVE_URL,
        "query": {"category": "software-dev"},
        "items_path": "jobs",
        "mapper": "remotive",
        "max_items": 80,
        "location_include": list(REMOTIVE_EU_TOKENS),
        "category_include": [
            "software",
            "data",
            "devops",
            "qa",
            "sysadmin",
            "engineering",
        ],
    },
    "landing_jobs": {
        "url": LANDING_JOBS_URL,
        "mapper": "landing_jobs",
        "max_items": 80,
    },
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


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = [str(value)]
    out: set[str] = set()
    for part in parts:
        for tok in re.split(r"[^\w]+", part.lower()):
            if tok:
                out.add(tok)
    return out


def _employment_wanted(cfg: dict[str, Any] | None) -> set[str]:
    if not cfg:
        return set()
    raw = cfg.get("employment")
    if raw is None:
        return set()
    values = raw if isinstance(raw, list) else [raw]
    wanted = {str(v).strip().lower() for v in values if str(v).strip()}
    wanted.discard("any")
    return wanted


def _employment_allowed(cfg: dict[str, Any] | None, engagement: str | None) -> bool:
    wanted = _employment_wanted(cfg)
    if not wanted:
        return True
    return (engagement or "").lower() in wanted


def _location_allowed(cfg: dict[str, Any] | None, location: str | None) -> bool:
    if not cfg:
        return True
    needles = cfg.get("location_include")
    if not needles:
        return True
    tokens = _tokens(location)
    wanted = {str(n).strip().lower() for n in needles if str(n).strip()}
    if not wanted:
        return True
    if tokens & wanted:
        return True
    blob = f" {(location or '').lower()} "
    return any(f" {n} " in blob or blob.strip() == n for n in wanted)


def _category_allowed(cfg: dict[str, Any] | None, category: str | None) -> bool:
    if not cfg:
        return True
    needles = cfg.get("category_include")
    if not needles:
        return True
    tokens = _tokens(category)
    wanted = {str(n).strip().lower() for n in needles if str(n).strip()}
    if not wanted:
        return True
    return bool(tokens & wanted)


def _justjoin_listing(
    item: dict[str, Any], cfg: dict[str, Any] | None = None
) -> RawListing | None:
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
    if not _employment_allowed(cfg, engagement):
        return None
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


def _remotive_pay(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    compact = text.lower().replace(" ", "")
    unit = None
    if "/hour" in compact or "/hr" in compact or "perhour" in compact:
        unit = "hour"
    elif "/day" in compact or "perday" in compact:
        unit = "day"
    elif "/month" in compact or "permonth" in compact:
        unit = "month"
    elif "/year" in compact or "peryear" in compact:
        unit = "year"
    else:
        return None
    currency = None
    if "$" in text:
        currency = "USD"
    elif "€" in text or "eur" in compact:
        currency = "EUR"
    elif "£" in text or "gbp" in compact:
        currency = "GBP"
    elif "pln" in compact:
        currency = "PLN"
    if not currency:
        return None
    nums = re.findall(r"(\d+(?:[.,]\d+)?)", text.replace(",", ""))
    if not nums:
        return None
    try:
        amount = float(nums[0].replace(",", "."))
    except ValueError:
        return None
    if amount <= 0:
        return None
    return {"amount": amount, "currency": currency, "unit": unit}


def _remotive_engagement(job_type: Any) -> str | None:
    text = str(job_type or "").strip().lower().replace(" ", "_")
    if text in {"freelance", "contract", "contractor", "b2b"}:
        return "b2b"
    if text in {"full_time", "full-time", "fulltime", "permanent", "employee"}:
        return "fte"
    return None


def _remotive_listing(
    item: dict[str, Any], cfg: dict[str, Any] | None = None
) -> RawListing | None:
    title = str(item.get("title") or "").strip()
    company = str(item.get("company_name") or item.get("company") or "").strip()
    if not title or not company:
        return None
    location = str(item.get("candidate_required_location") or "").strip()
    if not _location_allowed(cfg, location):
        return None
    if not _category_allowed(cfg, item.get("category")):
        return None
    engagement = _remotive_engagement(item.get("job_type"))
    if not _employment_allowed(cfg, engagement):
        return None
    url = str(item.get("url") or "").strip() or None
    guid = str(item.get("id") or "").strip()
    quoted = _remotive_pay(item.get("salary"))
    country = None
    loc_tokens = _tokens(location)
    if loc_tokens & {"poland", "polska"}:
        country = "PL"
    payload: dict[str, Any] = {
        "company": company,
        "title_posted": title,
        "source": "http_json",
        "url": url,
        "location_city": location or None,
        "location_country": country,
        "modality": "remote",
        "engagement": engagement,
        "experience_level": None,
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


def _landing_company(item: dict[str, Any], url: str | None) -> str:
    for key in ("company_name", "company"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            name = str(val.get("name") or "").strip()
            if name:
                return name
    if not url:
        return ""
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "at":
        return parts[1].replace("-", " ").title()
    return ""


def _landing_engagement(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    if any(tok in text for tok in ("freelance", "contract", "b2b", "contractor")):
        return "b2b"
    if any(tok in text for tok in ("full-time", "full time", "fulltime", "permanent")):
        return "fte"
    return None


def _landing_listing(
    item: dict[str, Any], cfg: dict[str, Any] | None = None
) -> RawListing | None:
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip() or None
    company = _landing_company(item, url)
    if not title or not company:
        return None
    engagement = _landing_engagement(item.get("type"))
    if not _employment_allowed(cfg, engagement):
        return None
    places = item.get("locations") if isinstance(item.get("locations"), list) else []
    city = None
    country = None
    for place in places:
        if not isinstance(place, dict):
            continue
        city = city or (str(place.get("city") or "").strip() or None)
        country = country or (str(place.get("country_code") or "").strip().upper() or None)
        if city and country:
            break
    modality = "remote" if item.get("remote") else "onsite"
    quoted = None
    amount = item.get("gross_salary_low")
    currency = str(item.get("currency_code") or "").upper() or None
    if amount is not None and currency:
        try:
            quoted = {
                "amount": float(amount),
                "currency": currency,
                "unit": "year",
            }
        except (TypeError, ValueError):
            quoted = None
    guid = str(item.get("id") or "").strip()
    payload: dict[str, Any] = {
        "company": company,
        "title_posted": title,
        "source": "http_json",
        "url": url,
        "location_city": city,
        "location_country": country,
        "modality": modality,
        "engagement": engagement,
        "experience_level": None,
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
    max_items = cfg.get("max_items")
    try:
        limit = int(max_items) if max_items not in (None, "") else 0
    except (TypeError, ValueError):
        limit = 0
    out: list[RawListing] = []
    for item in items:
        row = item if isinstance(item, dict) else {}
        if mapper == "justjoin":
            listing = _justjoin_listing(row, cfg)
        elif mapper == "remotive":
            listing = _remotive_listing(row, cfg)
        elif mapper == "landing_jobs":
            listing = _landing_listing(row, cfg)
        else:
            listing = _generic_listing(item, mapping)
        if listing:
            out.append(listing)
            if limit and len(out) >= limit:
                break
    return out
