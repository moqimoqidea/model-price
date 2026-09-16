"""Point-in-time catalogues, so a later scan can be compared with the last one.

A snapshot is what the previous scan saw. It is deliberately not the TTL cache:
cache entries expire after three hours and are *reused* within that window, while
a baseline has to survive until the next scan replaces it — an expiring baseline
would turn every run into a first run, and a reused one would hide a change behind
a cache hit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .core import write_json
from .models import normalize_model
from .paths import DEFAULT_SNAPSHOT_DIR, SNAPSHOT_SCHEMA_VERSION

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
        entry["offers"] = [unique[key] for key in sorted(unique)]
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
        (str(model["updated_at"]), _resolved(model["updated_at"]))
        for model in models
        if model.get("updated_at")
    ]
    resolved = [(stamp, moment) for stamp, moment in dated if moment is not None]
    if not resolved:
        return None
    return max(resolved, key=lambda item: item[1])[0]


def _resolved(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _offer_payload(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": offer.get("name", ""),
        "conditions": dict(offer.get("conditions") or {}),
        "prices": sorted(
            (
                {field: price.get(field) for field in PRICE_FIELDS}
                for price in offer.get("prices", [])
            ),
            key=price_identity,
        ),
    }


class SnapshotStore:
    """One baseline file per provider, replaced in place by each scan."""

    def __init__(self, root: Path = DEFAULT_SNAPSHOT_DIR) -> None:
        self.root = root

    def path(self, provider_id: str) -> Path:
        return self.root / f"{provider_id}.json"

    def read(self, provider_id: str) -> dict[str, Any] | None:
        """Return the last baseline, or ``None`` when there is none to compare with.

        A snapshot written by an older shape is treated as absent rather than
        diffed against, so a format change reports one baseline run instead of
        every model looking new.
        """
        try:
            payload = json.loads(self.path(provider_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            return None
        return payload

    def write(self, provider_id: str, snapshot: dict[str, Any]) -> None:
        write_json(self.path(provider_id), snapshot, indent=2)
