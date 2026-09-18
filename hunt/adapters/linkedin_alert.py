"""Parse LinkedIn job-alert subjects and bodies into canonical listing fields.

Does not invent pay or location. Subject ``X is hiring Y`` is a fallback.
"""

from __future__ import annotations

import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any


from hunt.core.pay import normalize_country, normalize_engagement

JOB_VIEW_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)",
    re.I,
)
HIRING_SUBJECT_RE = re.compile(
    r"^(?P<company>.+?) is hiring (?:an? |the )?(?P<title>.+)$",
    re.I,
)
AT_SUBJECT_RE = re.compile(
    r"^(?P<title>.+?) at (?P<company>.+?)(?::\s*(?P<rest>.+))?$",
    re.I,
)
JOBS_FOR_RE = re.compile(
    r"^(?:\d+\s+)?(?:new\s+)?jobs for(?: you)?(?::\s*|\s+)(?P<title>.+)$",
    re.I,
)
SUBJECT_PREFIX_RE = re.compile(
    r"^(?:job alert|new job|jobs for you)\s*:\s*",
    re.I,
)
VIEW_JOB_RE = re.compile(r"^view job:\s*(?P<url>\S+)", re.I)
SEPARATOR_RE = re.compile(r"^-{5,}$")
ACTIVELY_RE = re.compile(r"^this company is\b.*\bhiring\b", re.I)
CONNECTIONS_RE = re.compile(r"^\d+\s+company\b", re.I)
HEADERISH_RE = re.compile(
    r"^(your job alert|new jobs|see all jobs|manage your|unsubscribe|"
    r"job alert sent|this email|linkedin and the linkedin)\b",
    re.I,
)
MODALITY_RE = re.compile(
    r"\((?P<modality>remote|hybrid|on-?site)\)\s*$",
    re.I,
)
MODALITY_WORD_RE = re.compile(r"\b(remote|hybrid|on-?site|onsite)\b", re.I)
ENGAGEMENT_RE = re.compile(
    r"\b("
    r"full[\s\-_]?time|"
    r"part[\s\-_]?time|"
    r"permanent|"
    r"contract(?:or|ing)?|"
    r"freelance(?:r)?|"
    r"b2b|"
    r"uop|"
    r"self[\s\-_]?employed|"
    r"aut[oó]nomo"
    r")\b",
    re.I,
)
_DASH_TRANS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\xa0": " ",
        "\u202f": " ",
    }
)
PAY_RE = re.compile(
    r"""
    (?P<cur>€|\$|£|EUR|USD|GBP|CHF|PLN)
    \s*
    (?P<amount>\d[\d.,]*)
    \s*(?P<k>[kK])?
    (?:
        \s*[-–—]\s*
        (?:€|\$|£|EUR|USD|GBP|CHF|PLN)?\s*
        \d[\d.,]*\s*[kK]?
    )?
    \s*(?:/\s*|(?:\s+per\s+))
    (?P<unit>year|yr|years|month|mo|hour|hr|h|day)
    """,
    re.I | re.X,
)
DOT_SPLIT_RE = re.compile(r"\s+[·•]\s+")

_CURRENCY = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "EUR": "EUR",
    "USD": "USD",
    "GBP": "GBP",
    "CHF": "CHF",
    "PLN": "PLN",
}
_UNITS = {
    "year": "year",
    "yr": "year",
    "years": "year",
    "month": "month",
    "mo": "month",
    "hour": "hour",
    "hr": "hour",
    "h": "hour",
    "day": "day",
}
_MODALITY = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
    "on-site": "onsite",
    "on site": "onsite",
}
_ENGAGEMENT = {
    "full-time": "fte",
    "full time": "fte",
    "fulltime": "fte",
    "permanent": "fte",
    "uop": "fte",
    "part-time": None,
    "part time": None,
    "parttime": None,
    "contract": "b2b",
    "contractor": "b2b",
    "contracting": "b2b",
    "freelance": "b2b",
    "freelancer": "b2b",
    "b2b": "b2b",
    "self-employed": "b2b",
    "self employed": "b2b",
    "autonomo": "b2b",
    "autónomo": "b2b",
}


def _normalize_alert_text(text: str) -> str:
    return str(text).translate(_DASH_TRANS)


def _map_engagement(token: str) -> str | None:
    """Map a stated employment-type token to listing storage (fte|b2b).

    Applications keep b2b/uop aliases; pay treats b2b as freelance. Never
    guess: unmatched or part-time stays missing.
    """
    key = re.sub(r"[\s\-_]+", "-", token.lower()).strip("-")
    if key in _ENGAGEMENT:
        return _ENGAGEMENT[key]
    spaced = key.replace("-", " ")
    if spaced in _ENGAGEMENT:
        return _ENGAGEMENT[spaced]
    kind = normalize_engagement(token)
    if kind == "fte":
        return "fte"
    if kind == "freelance":
        return "b2b"
    return None


def extract_engagement(text: str | None) -> str | None:
    """Return fte|b2b when the posting states it. Never invent."""
    if not text:
        return None
    blob = _normalize_alert_text(text)
    for raw in ENGAGEMENT_RE.findall(blob):
        token = raw if isinstance(raw, str) else raw[0]
        mapped = _map_engagement(token)
        if mapped:
            return mapped
    return None


def canonical_job_url(value: str | None) -> str | None:
    if not value:
        return None
    match = JOB_VIEW_RE.search(value)
    if not match:
        return None
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}"


def parse_rfc822(raw: bytes | str) -> dict[str, Any]:
    blob = raw.encode("utf-8", errors="replace") if isinstance(raw, str) else raw
    msg = BytesParser(policy=policy.default).parsebytes(blob)
    return parse_message(msg)


def extract_bodies(msg: Message) -> tuple[str, str]:
    return _bodies(msg)


def parse_message(msg: Message) -> dict[str, Any]:
    subject = str(msg.get("Subject") or "").strip()
    text, html = extract_bodies(msg)
    return parse_alert(subject, text=text, html=html)


def parse_alert(
    subject: str,
    *,
    text: str = "",
    html: str = "",
    company_default: str | None = None,
) -> dict[str, Any]:
    """Return canonical listing fields. Missing pay/location stay absent."""
    subj = parse_subject(subject)
    cards = parse_text_cards(text)
    if not cards and html:
        cards = parse_html_cards(html)
    card = _pick_card(cards, subj)
    html_pay = _first_pay(html) if html else None
    text_pay = _first_pay(text) if text else None

    company = _clean((card or {}).get("company") or subj.get("company"))
    title = _clean((card or {}).get("title") or subj.get("title") or subject)
    url = (card or {}).get("url") or _first_job_url(text) or _first_job_url(html)
    location_city = (card or {}).get("location_city") or subj.get("location_city")
    location_country = (card or {}).get("location_country") or subj.get(
        "location_country"
    )
    modality = (card or {}).get("modality") or subj.get("modality")
    engagement = (card or {}).get("engagement") or subj.get("engagement")
    quoted = (card or {}).get("comp_quoted") or subj.get("comp_quoted")
    if quoted is None:
        quoted = text_pay or html_pay

    if not company:
        company = _clean(company_default) if company_default else None

    out: dict[str, Any] = {
        "company": company,
        "title": title,
        "url": url,
        "location_city": location_city,
        "location_country": location_country,
        "modality": modality,
        "engagement": engagement,
        "comp_quoted": quoted,
    }
    return {key: value for key, value in out.items() if value not in (None, "")}


def parse_subject(subject: str) -> dict[str, Any]:
    raw = " ".join((subject or "").split())
    if not raw:
        return {}
    cleaned = SUBJECT_PREFIX_RE.sub("", raw).strip()
    out: dict[str, Any] = {}
    hiring = HIRING_SUBJECT_RE.match(cleaned)
    if hiring:
        out["company"] = _clean(hiring.group("company"))
        title = _clean(hiring.group("title"))
        title, extra = _split_trailing_pay(title)
        out["title"] = title
        out.update(extra)
        return {k: v for k, v in out.items() if v}
    at = AT_SUBJECT_RE.match(cleaned)
    if at:
        out["title"] = _clean(at.group("title"))
        company = _clean(at.group("company"))
        rest = at.group("rest") or ""
        company, extra = _split_trailing_pay(company)
        out["company"] = company
        rest_fields = _fields_from_blob(rest)
        rest_fields.update(extra)
        out.update(rest_fields)
        return {k: v for k, v in out.items() if v}
    jobs_for = JOBS_FOR_RE.match(cleaned)
    if jobs_for:
        title, extra = _split_trailing_pay(_clean(jobs_for.group("title")))
        out["title"] = title
        out.update(extra)
        return {k: v for k, v in out.items() if v}
    extra = _fields_from_blob(cleaned)
    if extra:
        return extra
    return {}


def parse_text_cards(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    chunks = re.split(r"\n-{5,}\s*\n", text.replace("\r\n", "\n"))
    cards: list[dict[str, Any]] = []
    for chunk in chunks:
        card = _parse_text_card(chunk)
        if card and card.get("title") and card.get("url"):
            cards.append(card)
        elif card and card.get("title") and card.get("company"):
            cards.append(card)
    return cards


def parse_html_cards(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    parser = _JobHtml()
    try:
        parser.feed(html)
    except Exception:
        return []
    cards: list[dict[str, Any]] = []
    for job_id, texts in parser.cards:
        title = None
        company = None
        location_line = None
        blob_parts: list[str] = []
        for item in texts:
            blob_parts.append(item)
            if title is None and item and not _is_boilerplate(item):
                title = item
                continue
            if title and company is None and not _is_boilerplate(item):
                if "·" in item or "•" in item:
                    left, right = DOT_SPLIT_RE.split(item, 1)
                    company = _clean(left)
                    location_line = right
                else:
                    company = _clean(item)
                continue
            if title and company and location_line is None and not _is_boilerplate(item):
                location_line = item
        blob = "\n".join(blob_parts)
        extra = _fields_from_blob(blob)
        loc = _split_location(location_line) if location_line else {}
        card = {
            "title": _clean(title),
            "company": company,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}",
        }
        card.update(loc)
        card.update({k: v for k, v in extra.items() if k not in card or not card[k]})
        if card.get("title"):
            cards.append({k: v for k, v in card.items() if v not in (None, "")})
    return cards


def _parse_text_card(chunk: str) -> dict[str, Any] | None:
    lines = [_clean(ln) for ln in chunk.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln and not SEPARATOR_RE.match(ln)]
    if not lines:
        return None
    url = None
    body: list[str] = []
    for line in lines:
        view = VIEW_JOB_RE.match(line)
        if view:
            url = canonical_job_url(view.group("url")) or url
            continue
        found = canonical_job_url(line)
        if found:
            url = found
            continue
        if _is_boilerplate(line):
            continue
        body.append(line)
    if not body:
        return None
    title = body[0]
    company = body[1] if len(body) > 1 else None
    location_line = body[2] if len(body) > 2 else None
    if company and ("·" in company or "•" in company):
        left, right = DOT_SPLIT_RE.split(company, 1)
        company = _clean(left)
        location_line = right
    extra = _fields_from_blob("\n".join(body))
    loc = _split_location(location_line) if location_line else {}
    title, title_extra = _split_trailing_pay(title)
    card: dict[str, Any] = {
        "title": title,
        "company": _clean(company),
        "url": url,
    }
    card.update(loc)
    card.update(title_extra)
    card.update({k: v for k, v in extra.items() if k not in card or not card[k]})
    return {k: v for k, v in card.items() if v not in (None, "")}


def _pick_card(
    cards: list[dict[str, Any]], subject: dict[str, Any]
) -> dict[str, Any] | None:
    if not cards:
        return None
    company = (subject.get("company") or "").lower()
    title = (subject.get("title") or "").lower()
    if company:
        for card in cards:
            got = (card.get("company") or "").lower()
            if got and (got == company or got in company or company in got):
                return card
    if title:
        for card in cards:
            got = (card.get("title") or "").lower()
            if got and (got in title or title in got):
                return card
    return cards[0]


def _split_location(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    raw = _clean(text)
    modality = None
    match = MODALITY_RE.search(raw)
    if match:
        modality = _MODALITY.get(match.group("modality").lower().replace(" ", "-"))
        raw = _clean(raw[: match.start()])
    word = MODALITY_WORD_RE.fullmatch(raw) if raw else None
    if word:
        return {
            "modality": _MODALITY.get(word.group(1).lower().replace(" ", "-")),
        }
    extra = _fields_from_blob(raw)
    if extra.get("modality") and not modality:
        modality = extra["modality"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    city = None
    country = None
    if len(parts) == 1:
        if normalize_country(parts[0]):
            country = parts[0]
        else:
            city = parts[0]
    elif parts:
        if normalize_country(parts[-1]):
            country = parts[-1]
            city = ", ".join(parts[:-1]) or None
        else:
            city = ", ".join(parts)
    out: dict[str, Any] = {}
    if city:
        out["location_city"] = city
    if country:
        out["location_country"] = country
    if modality:
        out["modality"] = modality
    return out


def _fields_from_blob(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    out: dict[str, Any] = {}
    quoted = _first_pay(text)
    if quoted:
        out["comp_quoted"] = quoted
    modes = MODALITY_WORD_RE.findall(text)
    if modes:
        out["modality"] = _MODALITY.get(modes[0].lower().replace(" ", "-"))
    found = extract_engagement(text)
    if found:
        out["engagement"] = found
    return out


def _split_trailing_pay(text: str | None) -> tuple[str | None, dict[str, Any]]:
    cleaned = _clean(text)
    if not cleaned:
        return None, {}
    match = PAY_RE.search(cleaned)
    extra: dict[str, Any] = {}
    if match:
        extra["comp_quoted"] = _quoted_from_match(match)
        prefix = _clean(cleaned[: match.start()].rstrip(":-–— "))
        extra.update(_fields_from_blob(cleaned[match.start() :]))
        return prefix or cleaned, extra
    extra.update(_fields_from_blob(cleaned))
    return cleaned, extra


def _first_pay(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    match = PAY_RE.search(text)
    if not match:
        return None
    return _quoted_from_match(match)


def _quoted_from_match(match: re.Match[str]) -> dict[str, Any] | None:
    currency = _CURRENCY.get(match.group("cur").upper(), match.group("cur").upper())
    if match.group("cur") in _CURRENCY:
        currency = _CURRENCY[match.group("cur")]
    raw = match.group("amount").replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    if match.group("k"):
        amount *= 1000.0
    if amount <= 0:
        return None
    unit = _UNITS.get(match.group("unit").lower())
    if not unit:
        return None
    return {"amount": amount, "currency": currency, "unit": unit}


def _first_job_url(text: str | None) -> str | None:
    if not text:
        return None
    match = JOB_VIEW_RE.search(text)
    if not match:
        return None
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}"


def _is_boilerplate(line: str) -> bool:
    if not line:
        return True
    if HEADERISH_RE.match(line) or ACTIVELY_RE.match(line) or CONNECTIONS_RE.match(line):
        return True
    if VIEW_JOB_RE.match(line) or SEPARATOR_RE.match(line):
        return True
    if line.lower().startswith("see all jobs"):
        return True
    if len(line) > 280:
        return True
    return False


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(_normalize_alert_text(str(value)).split())
    return text or None


def _bodies(msg: Message) -> tuple[str, str]:
    text = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in {"text/plain", "text/html"}:
                continue
            payload = _part_text(part)
            if ctype == "text/plain" and not text:
                text = payload
            elif ctype == "text/html" and not html:
                html = payload
    else:
        payload = _part_text(msg)
        if msg.get_content_type() == "text/html":
            html = payload
        else:
            text = payload
    return text, html


def _part_text(part: Message) -> str:
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
    except Exception:
        pass
    raw = part.get_payload(decode=True)
    if isinstance(raw, (bytes, bytearray)):
        charset = part.get_content_charset() or "utf-8"
        return bytes(raw).decode(charset, errors="replace")
    if isinstance(raw, str):
        return raw
    return ""


class _JobHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[tuple[str, list[str]]] = []
        self._skip = 0
        self._current: tuple[str, list[str]] | None = None
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip:
            return
        data = {k: v or "" for k, v in attrs}
        extras = [
            _clean(data.get(key))
            for key in ("aria-label", "title", "alt")
            if data.get(key)
        ]
        extras = [item for item in extras if item]
        if tag == "a":
            job_id = _job_id(data.get("href"))
            if job_id and job_id not in self._seen:
                self._seen.add(job_id)
                self._current = (job_id, list(extras))
                self.cards.append(self._current)
                return
        if self._current:
            self._current[1].extend(extras)

    def handle_data(self, data: str) -> None:
        if self._skip or not self._current:
            return
        text = _clean(data)
        if text:
            self._current[1].append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1


def _job_id(href: str | None) -> str | None:
    if not href:
        return None
    match = JOB_VIEW_RE.search(href)
    return match.group(1) if match else None


