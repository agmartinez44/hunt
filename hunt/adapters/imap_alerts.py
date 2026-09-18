"""Read-only IMAP job-alert adapter. PEEK only. Never sends or deletes."""

from __future__ import annotations

import imaplib
import ssl
from email.header import decode_header
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from typing import Any, Callable

from hunt.adapters import RawListing
from hunt.adapters.linkedin_alert import extract_bodies, parse_alert
from hunt.core.errors import HuntError, ValidationError
from hunt.core.secrets import secret

_FETCH_SPEC = "(BODY.PEEK[])"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    parts = decode_header(str(value))
    out: list[str] = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(text))
    return " ".join(out).strip()


def _parse_headers(raw: str) -> dict[str, str]:
    headers = {"from": "", "subject": "", "date": "", "message-id": ""}
    current = None
    for line in raw.splitlines():
        if not line:
            continue
        if line[0] in " \t" and current:
            headers[current] += " " + line.strip()
            continue
        lower = line.lower()
        if lower.startswith("from:"):
            current = "from"
            headers[current] = line.split(":", 1)[1].strip()
        elif lower.startswith("subject:"):
            current = "subject"
            headers[current] = line.split(":", 1)[1].strip()
        elif lower.startswith("date:"):
            current = "date"
            headers[current] = line.split(":", 1)[1].strip()
        elif lower.startswith("message-id:"):
            current = "message-id"
            headers[current] = line.split(":", 1)[1].strip()
        else:
            current = None
    return {k: _decode(v) for k, v in headers.items()}


def _matches(text: str, needles: list[str]) -> bool:
    if not needles:
        return True
    blob = text.lower()
    return any(n.lower() in blob for n in needles if n)


def _imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _or_search(field: str, needles: list[str]) -> str:
    parts = [f"({field} {_imap_quote(n)})" for n in needles if n]
    if not parts:
        return "ALL"
    expr = parts[0]
    for part in parts[1:]:
        expr = f"(OR {part} {expr})"
    return expr


def _search_ids(client: Any, from_needles: list[str]) -> list[bytes]:
    criteria = _or_search("FROM", from_needles) if from_needles else "ALL"
    typ, data = client.search(None, criteria)
    if (typ != "OK" or not data or not data[0]) and criteria != "ALL":
        typ, data = client.search(None, "ALL")
    if typ != "OK" or not data or not data[0]:
        return []
    return list(data[0].split())


def _connect_ssl(host: str, port: int, user: str, password: str):
    context = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(host, port, ssl_context=context)
    try:
        client.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise HuntError("imap login failed") from exc
    return client


def _fetch_blob(fetched: Any) -> bytes:
    if not fetched:
        return b""
    for item in fetched:
        if isinstance(item, tuple) and len(item) > 1:
            part = item[1]
            if isinstance(part, (bytes, bytearray)):
                return bytes(part)
    return b""


def _listing_from_message(
    blob: bytes,
    *,
    company_default: str,
    from_needles: list[str],
    subject_needles: list[str],
) -> RawListing | None:
    msg = None
    headers: dict[str, str]
    text = ""
    html = ""
    if blob.strip():
        try:
            msg = BytesParser(policy=policy.default).parsebytes(blob)
        except Exception:
            msg = None
    if msg is not None:
        headers = {
            "from": _decode(msg.get("From")),
            "subject": _decode(msg.get("Subject")),
            "date": _decode(msg.get("Date")),
            "message-id": _decode(msg.get("Message-ID")),
        }
        text, html = extract_bodies(msg)
    else:
        headers = _parse_headers(blob.decode("utf-8", errors="replace"))
    frm = headers.get("from") or ""
    subject = headers.get("subject") or ""
    if not _matches(frm, from_needles) or not _matches(subject, subject_needles):
        return None
    if not subject:
        return None
    parsed = parse_alert(
        subject, text=text, html=html, company_default=company_default
    )
    title = str(parsed.get("title") or subject)
    company = str(parsed.get("company") or company_default)
    url = parsed.get("url")
    url = str(url) if url else None
    msg_key = headers.get("message-id") or ""
    if not msg_key:
        msg_key = sha256(f"{frm}|{subject}|{headers.get('date')}".encode()).hexdigest()[
            :16
        ]
    payload: dict[str, Any] = {
        "company": company,
        "title_posted": title,
        "source": "imap_alerts",
        "from": frm,
        "date": headers.get("date"),
    }
    if url:
        payload["url"] = url
    for key in (
        "location_city",
        "location_country",
        "modality",
        "engagement",
        "comp_quoted",
    ):
        if parsed.get(key) not in (None, ""):
            payload[key] = parsed[key]
    return RawListing(
        external_id=msg_key,
        title=title,
        company=company,
        url=url,
        payload=payload,
    )


def poll_imap_alerts(
    config: dict[str, Any],
    secrets: dict[str, str],
    *,
    connect: Callable[..., Any] | None = None,
) -> list[RawListing]:
    folder = str(config.get("folder") or "INBOX")
    host = config.get("host") or secret(
        secrets, str(config.get("host_env") or "IMAP_HOST"), "IMAP_HOST"
    )
    user = config.get("user") or secret(
        secrets, str(config.get("user_env") or "IMAP_USER"), "IMAP_USER"
    )
    password = secret(
        secrets, str(config.get("password_env") or "IMAP_PASSWORD"), "IMAP_PASSWORD"
    )
    port = int(config.get("port") or secret(secrets, "IMAP_PORT") or 993)
    if connect is None:
        if not host or not user or not password:
            raise ValidationError(
                "imap_alerts needs host/user and IMAP_PASSWORD (via secrets.env)"
            )
        client = _connect_ssl(str(host), port, str(user), str(password))
    else:
        client = connect()
    from_needles = [str(x) for x in (config.get("from_contains") or [])]
    subject_needles = [str(x) for x in (config.get("subject_contains") or [])]
    company_default = str(config.get("company_default") or "Job alert")
    limit = int(config.get("limit") or 50)
    listings: list[RawListing] = []
    try:
        typ, _ = client.select(folder, readonly=True)
        if typ != "OK":
            raise HuntError(f"imap folder not selectable: {folder}")
        ids = _search_ids(client, from_needles)
        for msg_id in ids:
            typ, fetched = client.fetch(msg_id, _FETCH_SPEC)
            if typ != "OK" or not fetched:
                continue
            blob = _fetch_blob(fetched)
            listing = _listing_from_message(
                blob,
                company_default=company_default,
                from_needles=from_needles,
                subject_needles=subject_needles,
            )
            if listing is None:
                continue
            listings.append(listing)
        listings = listings[-limit:]
    finally:
        logout = getattr(client, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:
                pass
    return listings
