"""Filesystem anchors and cache policy for the model-price skill."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PACKAGE_DIR.parent
SKILL_DIR = SCRIPTS_DIR.parent
DEFAULT_CACHE_DIR = SKILL_DIR / "cache"
DEFAULT_SNAPSHOT_DIR = SKILL_DIR / "snapshots"

CACHE_TTL = timedelta(hours=3)
# Bumped whenever a provider's source or parsing changes, so entries written by an
# older version are ignored instead of being served for the rest of their TTL.
CACHE_SCHEMA_VERSION = 5

# Snapshots are the baseline a later scan is compared against. They never expire —
# an expired baseline would turn every run into a first run — so they are versioned
# separately from the TTL cache. Bump this whenever the snapshot shape changes, and
# an older baseline is treated as absent instead of being diffed against.
SNAPSHOT_SCHEMA_VERSION = 1
