"""Domain errors. The CLI maps these to non-zero exits."""

from __future__ import annotations


class HuntError(Exception):
    """User-facing domain error."""

    exit_code = 1


class NotFoundError(HuntError):
    exit_code = 2


class ValidationError(HuntError):
    exit_code = 2
