"""Errors raised by price sources and by the skill self-updater."""

from __future__ import annotations


class SourceError(RuntimeError):
    """A provider's official source could not be fetched or parsed."""


class SkillUpdateError(RuntimeError):
    """The skill could not be fast-forwarded from its Git upstream."""
