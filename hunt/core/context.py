"""Request-scoped actor for the event log.

HTTP sets ``ui``, CLI leaves the default ``cli``, workers set ``job``.
"""

from __future__ import annotations

from contextvars import ContextVar

current_actor: ContextVar[str] = ContextVar("hunt_actor", default="cli")


def actor() -> str:
    value = current_actor.get()
    if value in {"ui", "cli", "mcp", "job"}:
        return value
    return "cli"
