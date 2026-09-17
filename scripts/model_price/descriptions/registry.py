"""Construct the independent model-description subsystem."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..caching import CacheStore
from ..paths import DEFAULT_CACHE_DIR
from .resolver import CachedDescriptionSource, DescriptionResolver
from .sources import DESCRIPTION_SOURCE_CLASSES, TencentMirrorDescriptionSource

DEFAULT_TENCENT_MIRROR = Path(__file__).with_name("data") / "tencent-models.json"


def build_description_resolver(
    client: Any,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
    tencent_mirror: Path = DEFAULT_TENCENT_MIRROR,
) -> DescriptionResolver:
    cache = CacheStore(cache_dir)
    raw = [source(client) for source in DESCRIPTION_SOURCE_CLASSES]
    raw.append(TencentMirrorDescriptionSource(client, tencent_mirror))
    return DescriptionResolver(
        {
            source.source_id: CachedDescriptionSource(
                source, cache, refresh=refresh
            )
            for source in raw
        }
    )
