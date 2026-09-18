"""Read-only IMAP job-alert adapter. PEEK only. Never sends or deletes."""

from __future__ import annotations

import imaplib
import ssl
from email.header import decode_header
from hashlib import sha256
from typing import Any, Callable

from hunt.adapters import RawListing
from hunt.core.errors import HuntError, ValidationError
from hunt.core.secrets import secret


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


def _connect_ssl(host: str, port: int, user: str, password: str):
    context = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(host, port, ssl_context=context)
    try:
        client.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise HuntError("imap login failed") from exc
    return client


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
        typ, data = client.search(None, "ALL")
        if typ != "OK" or not data:
            return []
        ids = data[0].split() if data[0] else []
        ids = ids[-limit:]
        for msg_id in ids:
            typ, fetched = client.fetch(
                msg_id,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
            )
            if typ != "OK" or not fetched:
                continue
            blob = b""
            first = fetched[0]
            if isinstance(first, tuple) and len(first) > 1:
                part = first[1]
                blob = part if isinstance(part, (bytes, bytearray)) else b""
            headers = _parse_headers(blob.decode("utf-8", errors="replace"))
            frm = headers.get("from") or ""
            subject = headers.get("subject") or ""
            if not _matches(frm, from_needles) or not _matches(subject, subject_needles):
                continue
            if not subject:
                continue
            msg_key = headers.get("message-id") or ""
            if not msg_key:
                msg_key = sha256(f"{frm}|{subject}|{headers.get('date')}".encode()).hexdigest()[:16]
            listings.append(
                RawListing(
                    external_id=msg_key,
                    title=subject,
                    company=company_default,
                    url=None,
                    payload={
                        "company": company_default,
                        "title_posted": subject,
                        "source": "imap_alerts",
                        "from": frm,
                        "date": headers.get("date"),
                    },
                )
            )
    finally:
        logout = getattr(client, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:
                pass
    return listings
