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
CACHE_SCHEMA_VERSION = 9

# Snapshots are archived baselines rather than response caches, so they are
# versioned separately from the TTL cache. Bump this whenever their shape changes;
# an older baseline is then absent from comparisons instead of looking like every
# model changed.
SNAPSHOT_SCHEMA_VERSION = 1

# Successful scans are archived per provider. The hard count limit reserves the
# last scan of each day in the recent calendar window, then fills remaining slots
# with the newest scans for useful fine-grained and long-running history.
SNAPSHOT_RETENTION_MONTHS = 3
SNAPSHOT_RETENTION_COUNT = 1000
