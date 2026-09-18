"""Source adapters. Fetch listings only. Never submit forms or send mail."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from hunt.core.errors import ValidationError
from hunt.core.sources import Source
from hunt.core.workspace import Workspace

SOURCE_KINDS = ("imap_alerts", "http_json")


@dataclass
class RawListing:
    external_id: str
    title: str
    company: str
    url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def poll_source(
    ws: Workspace,
    source: Source,
    *,
    get_json: Callable[..., Any] | None = None,
    imap_connect: Callable[..., Any] | None = None,
) -> list[RawListing]:
    if source.kind == "http_json":
        from hunt.adapters.http_json import poll_http_json

        return poll_http_json(
            source.config, workspace_root=ws.root, get_json=get_json
        )
    if source.kind == "imap_alerts":
        from hunt.adapters.imap_alerts import poll_imap_alerts

        return poll_imap_alerts(
            source.config, ws.secrets(), connect=imap_connect
        )
    raise ValidationError(f"unknown source kind: {source.kind}")
