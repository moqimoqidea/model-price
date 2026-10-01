"""Expose independent official release monitoring to the scan orchestrator."""

from __future__ import annotations

from .core import announcement_description, scan_announcements

__all__ = ["announcement_description", "scan_announcements"]
