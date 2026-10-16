"""Errors raised by price sources and by the skill self-updater."""

from __future__ import annotations


class SourceError(RuntimeError):
    """A provider's official source could not be fetched or parsed."""


class HttpSourceError(SourceError):
    """Keep HTTP routing facts without making callers parse diagnostic prose."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status: int,
        verification: str | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.verification = verification


class SkillUpdateError(RuntimeError):
    """The skill could not be fast-forwarded from its Git upstream."""
