"""Filesystem anchors and cache policy for the model-price skill."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PACKAGE_DIR.parent
SKILL_DIR = SCRIPTS_DIR.parent
DEFAULT_CACHE_DIR = SKILL_DIR / "cache"

CACHE_TTL = timedelta(hours=3)
# Bumped whenever a provider's source or parsing changes, so entries written by an
# older version are ignored instead of being served for the rest of their TTL.
CACHE_SCHEMA_VERSION = 4
