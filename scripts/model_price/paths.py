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
CACHE_SCHEMA_VERSION = 27

# Snapshots are archived baselines rather than response caches, so they are
# versioned separately from the TTL cache. Bump this when a shape change would make
# an older baseline compare wrongly; an older baseline is then absent from
# comparisons instead of looking like every model changed. Reading one region per
# model rather than every region a cloud prices is such a change: a model's offers
# are named and grouped differently, so a baseline written before it would report
# every cloud offer as replaced rather than report a price. A change that only adds
# an optional field no comparison reads does not need a bump — a baseline without it
# reads as ``None``, which is what the field's absence already means. Recording a
# unit as a code rather than as whatever a vendor wrote is the kind of change that
# does: every price whose unit wording was kept verbatim would otherwise be read as
# having moved.
SNAPSHOT_SCHEMA_VERSION = 4
# Retirement notices have their own archived shape under lifecycle-<provider>.
# Keep their compatibility gate independent from price catalogue baselines.
LIFECYCLE_SCHEMA_VERSION = 2
# Model announcements are evidence of publication, independently of price rows.
ANNOUNCEMENT_SCHEMA_VERSION = 1

# Successful scans are archived per provider. The hard count limit reserves the
# last scan of each day in the recent calendar window, then fills remaining slots
# with the newest scans for useful fine-grained and long-running history.
SNAPSHOT_RETENTION_MONTHS = 3
SNAPSHOT_RETENTION_COUNT = 1000
