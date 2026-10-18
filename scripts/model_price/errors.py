"""Errors raised by price sources and by the skill self-updater."""

from __future__ import annotations

from .privacy import safe_diagnostic


class _PublicDiagnosticError(RuntimeError):
    """Error text is safe to reuse in a report, not an unfiltered transport log."""

    def __init__(self, message: str) -> None:
        super().__init__(safe_diagnostic(message))


class SourceError(_PublicDiagnosticError):
    """A provider's official source could not be fetched or parsed."""


class RequestBudgetExceeded(SourceError):
    """The run's per-host request budget stopped a read before it was attempted."""


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
        self.url = safe_diagnostic(url)
        self.status = status
        self.verification = verification


class SkillUpdateError(_PublicDiagnosticError):
    """The skill could not be fast-forwarded from its Git upstream."""
