"""Archived point-in-time catalogues for latest and historical comparisons.

Snapshots are deliberately not the TTL cache: cached responses are reused for
three hours, which would hide a catalogue change, while every successful scan is
an independent baseline. The store has a hard per-provider count bound while
reserving daily first and last anchors for recent calendar comparisons.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import uuid4

from .core import write_json
from .models import normalize_model
from .parsing import non_token_billing_header
from .paths import (
    DEFAULT_SNAPSHOT_DIR,
    SNAPSHOT_RETENTION_COUNT,
    SNAPSHOT_RETENTION_MONTHS,
    SNAPSHOT_SCHEMA_VERSION,
)
from .pricing import offer_priority, price_sort_key

LATEST = "latest"
YESTERDAY = "yesterday"
YESTERDAY_FIRST = "yesterday_first"
LAST_MONTH = "last_month"
ON_DATE = "date"
AT_OR_BEFORE = "at_or_before"

CALENDAR_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
CALENDAR_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]")


@dataclass(frozen=True)
class BaselineSelection:
    """A user request for the baseline one provider should be compared with."""

    mode: str = LATEST
    requested: str | None = None
    day: date | None = None
    moment: datetime | None = None

    @property
    def is_latest(self) -> bool:
        return self.mode == LATEST

    def payload(self) -> dict[str, Any]:
        target: str | None = None
        uses_scan_timezone = False
        if self.day is not None:
            target = self.day.isoformat()
        elif self.moment is not None:
            target = self.moment.isoformat()
            uses_scan_timezone = self.moment.tzinfo is None
        return {
            "mode": self.mode,
            "requested": self.requested,
            "target": target,
            "uses_scan_timezone": uses_scan_timezone,
        }


def parse_baseline_selection(value: str | None) -> BaselineSelection:
    """Parse relative aliases or an extended ISO calendar date or timestamp."""
    if value is None or value.strip().lower() == LATEST:
        return BaselineSelection()
    requested = value.strip()
    alias = requested.lower().replace("_", "-")
    if alias == YESTERDAY:
        return BaselineSelection(YESTERDAY, requested)
    if alias == "yesterday-first":
        return BaselineSelection(YESTERDAY_FIRST, requested)
    if alias == "last-month":
        return BaselineSelection(LAST_MONTH, requested)
    try:
        if CALENDAR_DATE.fullmatch(requested):
            return BaselineSelection(
                ON_DATE, requested, day=date.fromisoformat(requested)
            )
        if not CALENDAR_TIMESTAMP.match(requested):
            raise ValueError
        moment = datetime.fromisoformat(requested.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "expected yesterday, yesterday-first, last-month, YYYY-MM-DD, or an ISO calendar timestamp"
        ) from exc
    return BaselineSelection(AT_OR_BEFORE, requested, moment=moment)


# Conditions describing how the vendor laid the document out, rather than what is
# being billed. They are kept for the reader but stay out of an offer's identity:
# renaming a section must not read as one offer vanishing and another appearing.
NON_IDENTITY_CONDITIONS = frozenset({"source_section"})

PRICE_FIELDS = ("type", "label", "amount", "unit")


def offer_identity(offer: dict[str, Any]) -> tuple[Any, ...]:
    """Identify an offer by what it bills, independent of the document's layout."""
    conditions = {
        key: value
        for key, value in (offer.get("conditions") or {}).items()
        if key not in NON_IDENTITY_CONDITIONS
    }
    return (
        offer.get("name", ""),
        json.dumps(conditions, ensure_ascii=False, sort_keys=True),
    )


def price_identity(price: dict[str, Any]) -> tuple[str, str]:
    """Identify a price line by the charge it names.

    The label takes part: a charge renamed from 输入 to 输入（未命中缓存） is a
    different charge, not the same one with a new caption.
    """
    return (str(price.get("type", "")), str(price.get("label", "")))


def build_snapshot(
    provider: Any, records: Iterable[dict[str, Any]], captured_at: str
) -> dict[str, Any]:
    """Reduce one provider's scan to the baseline a later scan is compared with.

    Everything that varies for reasons other than a price is dropped or sorted, so
    identical source data yields an identical snapshot. A snapshot that differed
    run to run would make every comparison report a change.

    Models the source lists without a single parseable price are left out: they
    carry no price to compare, and the report's subject is the price catalogue.

    A source that answers with the same model twice contributes both answers: the
    offers are merged and deduplicated by identity, so an adapter that splits one
    model across several records cannot silently lose half its prices.
    """
    models: dict[str, dict[str, Any]] = {}
    for record in records:
        key = normalize_model(record["model_id"])
        offers = [
            _offer_payload(offer)
            for offer in record.get("offers", [])
            if offer.get("prices")
        ]
        if not key or not offers:
            continue
        entry = models.setdefault(
            key,
            {
                "model_id": record["model_id"],
                "display_name": record.get("display_name", record["model_id"]),
                "delivery_mode": record.get("delivery_mode"),
                "region": record.get("region"),
                "currency": record.get("currency"),
                "updated_at": record.get("source_updated_at"),
                "offers": [],
            },
        )
        entry["offers"].extend(offers)
    for entry in models.values():
        unique = {offer_identity(offer): offer for offer in entry["offers"]}
        # ``sorted`` is stable, so equal-priority context and time bands retain
        # the order in which the official source published them. This keeps a
        # shortest-context row ahead of its larger tiers without sacrificing the
        # explicit standard-before-batch ordering above it.
        entry["offers"] = sorted(unique.values(), key=offer_priority)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "provider": {"id": provider.provider_id, "name": provider.provider_name},
        "captured_at": captured_at,
        "source": {
            "url": provider.source_url,
            "kind": provider.source_kind,
            "updated_at": latest_update(models.values()),
        },
        "models": {key: models[key] for key in sorted(models)},
    }


def latest_update(models: Iterable[dict[str, Any]]) -> str | None:
    """Return the newest update stamp the provider published, in its own wording.

    Only some vendors date their catalogue. An empty result means this one does
    not, never that its prices are old.
    """
    dated = [
        (str(model["updated_at"]), _parse_moment(model["updated_at"]))
        for model in models
        if model.get("updated_at")
    ]
    resolved = [(stamp, moment) for stamp, moment in dated if moment is not None]
    if not resolved:
        return None
    return max(resolved, key=lambda item: item[1])[0]


def _offer_payload(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": offer.get("name", ""),
        "conditions": dict(offer.get("conditions") or {}),
        "prices": sorted(
            (
                {field: price.get(field) for field in PRICE_FIELDS}
                for price in offer.get("prices", [])
            ),
            key=price_sort_key,
        ),
    }


SnapshotRef = tuple[datetime, Path]


class SnapshotStore:
    """A timestamped history with a hard file-count bound per provider."""

    def __init__(
        self,
        root: Path = DEFAULT_SNAPSHOT_DIR,
        *,
        retention_months: int = SNAPSHOT_RETENTION_MONTHS,
        retention_count: int = SNAPSHOT_RETENTION_COUNT,
    ) -> None:
        if retention_months < 1 or retention_count < 1:
            raise ValueError("snapshot retention values must be positive")
        self.root = root
        self.retention_months = retention_months
        self.retention_count = retention_count
        self._refs: dict[str, list[SnapshotRef]] = {}
        self._payloads: dict[Path, dict[str, Any] | None] = {}

    def path(self, provider_id: str) -> Path:
        """Return the legacy single-baseline path used before history existed."""
        return self.root / f"{provider_id}.json"

    def history_dir(self, provider_id: str) -> Path:
        return self.root / provider_id

    def read(self, provider_id: str) -> dict[str, Any] | None:
        """Return the latest valid baseline, including a legacy single file."""
        return self._last_valid(self._entry_refs(provider_id))

    def iter_history(self, provider_id: str) -> Iterator[dict[str, Any]]:
        """Yield valid baselines in capture order without loading them together."""
        for _, path in self._entry_refs(provider_id):
            payload = self._payloads.get(path)
            if path not in self._payloads:
                payload = _read_snapshot(path)
            if payload:
                yield payload

    def select(
        self,
        provider_id: str,
        selection: BaselineSelection,
        *,
        reference_at: str | datetime,
    ) -> dict[str, Any] | None:
        """Resolve one baseline relative to the current scan's local calendar."""
        entries = self._entry_refs(provider_id)
        if not entries:
            return None
        if selection.is_latest:
            return self._last_valid(entries)

        reference = require_moment(reference_at, label="scan timestamp")
        local_zone = reference.tzinfo or timezone.utc

        def local_moment(entry: SnapshotRef) -> datetime:
            return entry[0].astimezone(local_zone)

        candidates = entries
        if selection.mode in (YESTERDAY, YESTERDAY_FIRST):
            target_day = reference.date() - timedelta(days=1)
            candidates = [
                entry for entry in entries if local_moment(entry).date() == target_day
            ]
        elif selection.mode == LAST_MONTH:
            target_month = reference.month - 1 or 12
            target_year = reference.year - (1 if reference.month == 1 else 0)
            candidates = []
            for entry in entries:
                local = local_moment(entry)
                if (local.year, local.month) == (target_year, target_month):
                    candidates.append(entry)
        elif selection.mode == ON_DATE:
            candidates = [
                entry
                for entry in entries
                if local_moment(entry).date() == selection.day
            ]
        elif selection.mode == AT_OR_BEFORE:
            target = selection.moment
            if target is None:
                return None
            if target.tzinfo is None:
                target = target.replace(tzinfo=local_zone)
            candidates = [entry for entry in entries if entry[0] <= target]
        else:
            raise ValueError(f"unknown baseline selection mode: {selection.mode}")
        if selection.mode == YESTERDAY_FIRST:
            for _, path in candidates:
                if payload := self._read(path):
                    return payload
            return None
        return self._last_valid(candidates)

    def write(self, provider_id: str, snapshot: dict[str, Any]) -> None:
        """Archive one successful scan, migrate legacy state, then prune history."""
        moment = require_moment(snapshot.get("captured_at"), label="snapshot timestamp")
        entries = list(self._entry_refs(provider_id))
        destination = self._archive_path(provider_id, moment)
        write_json(destination, snapshot, indent=2)
        self._payloads[destination] = snapshot
        entries.append((moment, destination))
        try:
            entries = self._migrate_legacy(provider_id, entries)
            retained = self._prune(provider_id, entries, moment)
        except Exception:
            # The archive already exists. Force a fresh directory read so this
            # store cannot keep serving an index from before the successful write.
            self._refs.pop(provider_id, None)
            raise
        self._refs[provider_id] = retained

    def _entry_refs(self, provider_id: str) -> list[SnapshotRef]:
        """List capture times once per provider without loading every catalogue."""
        if provider_id in self._refs:
            return self._refs[provider_id]
        entries: list[SnapshotRef] = []
        legacy = self.path(provider_id)
        if legacy_moment := _snapshot_moment(self._read(legacy)):
            entries.append((legacy_moment, legacy))
        directory = self.history_dir(provider_id)
        if directory.exists():
            entries.extend(
                (moment, path)
                for path in directory.glob("*.json")
                if (moment := _archive_moment(path)) is not None
            )
        self._refs[provider_id] = sorted(entries, key=_entry_order)
        return self._refs[provider_id]

    def _read(self, path: Path) -> dict[str, Any] | None:
        if path not in self._payloads:
            self._payloads[path] = _read_snapshot(path)
        return self._payloads[path]

    def _last_valid(self, entries: list[SnapshotRef]) -> dict[str, Any] | None:
        for _, path in reversed(entries):
            if payload := self._read(path):
                return payload
        return None

    def _migrate_legacy(
        self, provider_id: str, entries: list[SnapshotRef]
    ) -> list[SnapshotRef]:
        legacy = self.path(provider_id)
        if not legacy.exists():
            return entries
        payload = self._read(legacy)
        moment = _snapshot_moment(payload)
        if payload is None or moment is None:
            if self._quarantine_legacy(provider_id, legacy):
                return [entry for entry in entries if entry[1] != legacy]
            return entries
        same_moment = [
            path
            for captured_at, path in entries
            if path != legacy and captured_at == moment
        ]
        if not any(self._read(path) == payload for path in same_moment):
            destination = self._archive_path(provider_id, moment)
            write_json(destination, payload, indent=2)
            self._payloads[destination] = payload
            entries.append((moment, destination))
        try:
            legacy.unlink()
        except OSError:
            return sorted(entries, key=_entry_order)
        self._payloads.pop(legacy, None)
        return sorted(
            (entry for entry in entries if entry[1] != legacy), key=_entry_order
        )

    def _quarantine_legacy(self, provider_id: str, legacy: Path) -> bool:
        rejected = self.root / "rejected"
        destination = rejected / f"{provider_id}-{uuid4().hex}.json"
        try:
            rejected.mkdir(parents=True, exist_ok=True)
            legacy.replace(destination)
        except OSError:
            return False
        self._payloads.pop(legacy, None)
        return True

    def _archive_path(self, provider_id: str, moment: datetime) -> Path:
        stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return self.history_dir(provider_id) / f"{stamp}-{uuid4().hex}.json"

    def _prune(
        self, provider_id: str, entries: list[SnapshotRef], reference: datetime
    ) -> list[SnapshotRef]:
        directory = self.history_dir(provider_id)
        archives = sorted(
            (entry for entry in entries if entry[1].parent == directory),
            key=_entry_order,
        )
        if len(archives) <= self.retention_count:
            return sorted(entries, key=_entry_order)

        cutoff = _subtract_months(reference, self.retention_months)
        local_zone = reference.tzinfo or timezone.utc
        daily_first: dict[date, SnapshotRef] = {}
        daily_last: dict[date, SnapshotRef] = {}
        for entry in archives:
            if entry[0] >= cutoff:
                day = entry[0].astimezone(local_zone).date()
                daily_first.setdefault(day, entry)
                daily_last[day] = entry
        latest_anchors = sorted(daily_last.values(), key=_entry_order)
        kept = {path for _, path in latest_anchors[-self.retention_count :]}
        for _, path in reversed(sorted(daily_first.values(), key=_entry_order)):
            if len(kept) >= self.retention_count:
                break
            kept.add(path)
        for _, path in reversed(archives):
            if len(kept) >= self.retention_count:
                break
            kept.add(path)

        retained = [entry for entry in entries if entry[1].parent != directory]
        for entry in archives:
            path = entry[1]
            if path in kept:
                retained.append(entry)
                continue
            try:
                path.unlink()
            except OSError:
                retained.append(entry)
            else:
                self._payloads.pop(path, None)
        return sorted(retained, key=_entry_order)


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return None
    return _without_mislabeled_token_prices(payload)


def _without_mislabeled_token_prices(payload: dict[str, Any]) -> dict[str, Any]:
    """Ignore non-token rows misclassified in older price baselines.

    Their original source headers remain in each price label, so the correction
    can be applied on read without rewriting an archived historical document.
    Retirement archives have a different model shape and are left untouched.
    """
    if (payload.get("source") or {}).get("kind") == "official_retirement_notice":
        return payload
    models = payload.get("models")
    if not isinstance(models, dict):
        return payload
    cleaned_models: dict[str, Any] = {}
    changed = False
    for key, model in models.items():
        if not isinstance(model, dict) or not isinstance(model.get("offers"), list):
            cleaned_models[key] = model
            continue
        offers = []
        for offer in model["offers"]:
            prices = [
                price for price in offer.get("prices", [])
                if not (
                    str(price.get("unit", "")).endswith("_per_million_tokens")
                    and non_token_billing_header(str(price.get("label", "")))
                )
            ]
            if len(prices) != len(offer.get("prices", [])):
                changed = True
            if prices:
                offers.append({**offer, "prices": prices})
            else:
                changed = True
        if offers:
            cleaned_models[key] = {**model, "offers": offers}
        else:
            changed = True
    return {**payload, "models": cleaned_models} if changed else payload


def _archive_moment(path: Path) -> datetime | None:
    stamp = path.name.split("-", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _entry_order(entry: tuple[datetime, Path]) -> tuple[datetime, int, str]:
    try:
        modified_at = entry[1].stat().st_mtime_ns
    except OSError:
        modified_at = 0
    return entry[0], modified_at, entry[1].name


def _snapshot_moment(snapshot: dict[str, Any] | None) -> datetime | None:
    if not snapshot:
        return None
    return _parse_moment(snapshot.get("captured_at"))


def _parse_moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def require_moment(value: Any, *, label: str = "timestamp") -> datetime:
    """Return a parsed moment or reject an invalid caller-supplied timestamp."""
    moment = value if isinstance(value, datetime) else _parse_moment(value)
    if moment is None:
        raise ValueError(f"invalid {label}: {value}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _subtract_months(moment: datetime, months: int) -> datetime:
    ordinal = moment.year * 12 + moment.month - 1 - months
    year, zero_based_month = divmod(ordinal, 12)
    month = zero_based_month + 1
    day = min(moment.day, monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)
