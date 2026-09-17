"""Official model introductions used by queries and catalogue-change reports."""

from .core import AVAILABLE, NOT_FOUND, SOURCE_ERROR, DescriptionSource
from .registry import build_description_resolver
from .resolver import DescriptionResolver, query_targets

__all__ = (
    "AVAILABLE",
    "NOT_FOUND",
    "SOURCE_ERROR",
    "DescriptionResolver",
    "DescriptionSource",
    "build_description_resolver",
    "query_targets",
)
