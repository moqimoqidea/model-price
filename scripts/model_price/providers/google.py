"""Google Gemini pricing, read from the official Markdown pricing page.

The page publishes a Markdown copy at ``pricing.md.txt`` whose tables keep one
header row each, so the shared Markdown reader is enough: no element or class
name has to be tracked in rendered HTML.
"""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, model_matches, normalize_model
from ..parsing import (
    cell_rates,
    dated_rate_terms,
    markdown_link_text,
    markdown_tables,
    price_unit_code,
)
from ..pricing import (
    is_free_amount,
    make_record,
    price_item,
    unit_code,
    unit_measure,
    usd_amount,
    usd_amounts,
    usd_price,
)
from ..text import clean_text

GEMINI_URL = "https://ai.google.dev/gemini-api/docs/pricing"
GEMINI_MARKDOWN_URL = f"{GEMINI_URL}.md.txt"

# Sections that describe tools, agents, or general notes rather than a model.
NON_MODEL_SECTIONS = {"notes", "pricing-for-tools", "pricing-for-agents"}

PRICE_TYPE_BY_LABEL = {
    "input price": "input",
    "output price": "output",
    "context caching price": "cache_hit",
}

# A cache row publishes the hit price and the hourly storage price in one cell,
# so the storage clause is matched whole and removed from the hit price.
CACHE_STORAGE_RE = re.compile(
    r"\$(\d+(?:\.\d+)?)\s*/\s*1,000,000 tokens per hour[^.]*(?:\.|$)"
)

# A model section lists its API ids on an italic link line, either alone
# (``*[`gemini-3.8-flash`](https://...)*``) or as a whole family
# (``*[`veo-3.1-generate-preview`](...), [`veo-3.1-fast-generate-preview`](...)*``).
MODEL_ID_LINE_RE = re.compile(r"\[`([^`]+)`\]\(")

# A model that bills tokens also publishes what one call works out to in the unit
# its customers buy in, either in brackets beside the token rate ("$6.50 ($0.00016
# per second)") or as a whole-cell rate ("$0.039 per image"). Both are the vendor's
# own rates for the same charge, so both are kept, each in the unit it was written
# in, and the resolution an equivalent is quoted at stays in its label.
PARENTHETICAL_RATE_RE = re.compile(
    r"\(\s*\$(?P<amount>\d+(?:\.\d+)?)\s+(?P<unit>per\s+[a-z]+)\s*\)", re.I
)
EQUIVALENT_RATE_RE = re.compile(
    r"\$\s*(?P<amount>\d+(?:\.\d+)?)\s+per\s+(?P<size>[\d.]+K)\s*"
    r"(?:resolution\s+)?image",
    re.I,
)


def id_words(model_id: str) -> set[str]:
    """The words an API id is written with, for matching it to a published row."""
    return {word for word in re.split(r"[-_.\s]+", normalize_model(model_id)) if word}


def variant_words(display_name: str) -> set[str]:
    return id_words(normalize_model(display_name))


def assign_variant_rows(
    model_ids: list[str], labels: list[str]
) -> dict[str, list[str]] | None:
    """Match a family's rows to the API ids they price, or give up.

    A family publishes its ids on one line and prices its variants one row at a
    time ("Veo 3.1 Fast …" for ``veo-3.1-fast-generate-preview``). The id that
    carries no variant word of its own is the family's default and takes the row
    that carries none either. When a row cannot be claimed by exactly one id the
    whole section is left alone rather than guessed at: attaching a price to the
    wrong model is worse than reporting the family without one.
    """
    if not labels or not model_ids:
        return None
    if len(model_ids) == 1:
        return {model_ids[0]: list(labels)}
    if len(labels) < len(model_ids):
        # Fewer rows than ids: the page is pricing the family as a whole under one
        # row, so every id it lists shares that price rather than one of them
        # taking it alone.
        return None
    distinct = {
        model_id: id_words(model_id)
        - {word for other in model_ids if other != model_id for word in id_words(other)}
        for model_id in model_ids
    }
    assigned: dict[str, list[str]] = {model_id: [] for model_id in model_ids}
    remaining = list(labels)
    for model_id, words in distinct.items():
        for word in sorted(words):
            match = next(
                (label for label in remaining if word in variant_words(label)), None
            )
            if match:
                assigned[model_id].append(match)
                remaining.remove(match)
    defaults = [model_id for model_id in model_ids if not distinct[model_id]]
    if len(defaults) > 1 or (remaining and not defaults):
        return None
    if defaults:
        assigned[defaults[0]].extend(remaining)
    return {model_id: labels for model_id, labels in assigned.items() if labels}


class GeminiAdapter(PriceSource):
    provider_id = "google"
    provider_name = "Google Gemini"
    source_url = GEMINI_URL
    source_kind = "official_markdown"

    def _sections(self) -> list[dict[str, Any]]:
        """Split the page into its ``h2`` model sections with their tier tables."""
        text = self.document(GEMINI_MARKDOWN_URL)
        parts = re.split(r"^## +(.+)$", text, flags=re.M)
        sections = []
        for heading, body in zip(parts[1::2], parts[2::2]):
            display_name = markdown_link_text(heading)
            if normalize_model(display_name) in NON_MODEL_SECTIONS:
                continue
            sections.append(
                {
                    "display_name": display_name,
                    "model_ids": self._model_ids(body, display_name),
                    "tables": markdown_tables(body),
                }
            )
        if not sections:
            raise SourceError("official pricing sections were not found")
        return sections

    @staticmethod
    def _model_ids(body: str, display_name: str) -> list[str]:
        """Read the API ids the section publishes, or fall back to its heading."""
        for line in body.splitlines():
            found = MODEL_ID_LINE_RE.findall(line)
            if found:
                return found
        # Some sections head the model itself, e.g. ``## [Gemma 4](url)``.
        return [normalize_model(display_name)]

    def _offers(
        self, tables: list[tuple[list[str], list[list[str]]]]
    ) -> list[dict[str, Any]]:
        offers = []
        for headings, rows in tables:
            if len(rows) < 2:
                continue
            tier = (headings[-1] if headings else "standard").lower()
            headers = [cell.lower() for cell in rows[0]]
            paid_index = next(
                (index for index, cell in enumerate(headers) if "paid tier" in cell),
                None,
            )
            if paid_index is None:
                continue
            free_index = next(
                (index for index, cell in enumerate(headers) if "free tier" in cell),
                None,
            )
            prices = []
            for row in rows[1:]:
                if len(row) <= paid_index:
                    continue
                label = row[0]
                kind = next(
                    (
                        value
                        for key, value in PRICE_TYPE_BY_LABEL.items()
                        if key in label.lower()
                    ),
                    None,
                )
                if not kind:
                    continue
                # The free tier prices what the paid tier leaves unpriced: a
                # model with "Free of charge" beside "Not available" is free,
                # while one the paid tier does quote keeps the paid rate.
                cell = row[paid_index]
                if (
                    free_index is not None
                    and len(row) > free_index
                    and not usd_amount(cell)
                ):
                    cell = row[free_index]
                storage = CACHE_STORAGE_RE.search(cell) if kind == "cache_hit" else None
                if storage:
                    cell = CACHE_STORAGE_RE.sub("", cell).strip()
                # A cell that is nothing but a rate in another unit prices that
                # unit; only a cell that leaves the unit to the page is a token
                # rate. Reading an image price as a token price would quote it a
                # million times over.
                item = usd_price(kind, label, cell, unit=self._cell_unit(cell))
                if item:
                    # A cell that dates the rate it bills prices a period rather than
                    # the model, so the day the amount stops and any rate that takes
                    # over are both kept: read as one amount, a limited-time rate
                    # would pass for what the model ordinarily costs and an increase
                    # the vendor has already announced would be lost with it.
                    item.update(dated_rate_terms(cell))
                    prices.append(item)
                    prices.extend(self._equivalent_prices(kind, label, cell))
                if storage:
                    storage_cell = clean_text(storage.group(0))
                    prices.append(
                        price_item(
                            "cache_storage",
                            "Context cache storage",
                            storage.group(1),
                            "USD_per_million_tokens_per_hour",
                            display=storage_cell,
                            **dated_rate_terms(storage_cell),
                        )
                    )
            if prices:
                offers.append(
                    {
                        "name": normalize_model(tier) or "standard",
                        "conditions": {
                            "service_tier": tier,
                            # A model the page prices entirely at nothing is
                            # billed on the free tier, whatever the table calls
                            # the column it came from.
                            "billing_tier": (
                                "free"
                                if all(is_free_amount(p["amount"]) for p in prices)
                                else "paid"
                            ),
                        },
                        "prices": prices,
                    }
                )
        return offers

    @staticmethod
    def _cell_unit(cell: str) -> str:
        """The unit a whole-cell rate is written in, or the token default."""
        measured = unit_measure(cell)
        if measured and len(usd_amounts(cell)) == 1:
            return unit_code(cell, "USD")
        return "USD_per_million_tokens"

    @staticmethod
    def _equivalent_prices(kind: str, label: str, cell: str) -> list[dict[str, Any]]:
        """The same charge restated in a unit the vendor also sells by."""
        prices = []
        for match in PARENTHETICAL_RATE_RE.finditer(cell):
            unit = unit_code(match["unit"], "USD")
            if unit == "USD_per_million_tokens":
                continue
            prices.append(
                price_item(
                    kind,
                    f"{label}（{clean_text(match['unit'])}）",
                    match["amount"],
                    unit,
                    display=clean_text(match.group(0)),
                )
            )
        for match in EQUIVALENT_RATE_RE.finditer(cell):
            prices.append(
                price_item(
                    kind,
                    f"{label}（Equivalent to {match['size']} image）",
                    match["amount"],
                    "USD_per_image",
                    display=clean_text(match.group(0)),
                )
            )
        return prices

    def _variant_offers(
        self, tables: list[tuple[list[str], list[list[str]]]]
    ) -> dict[str, list[dict[str, Any]]]:
        """The prices a family publishes one variant at a time, keyed by API id.

        Only a table that bills in something other than tokens prices variants:
        a token table prices charges, and its rows are read as such.
        """
        collected: list[tuple[str, list[dict[str, Any]]]] = []
        for headings, rows in tables:
            if len(rows) < 2:
                continue
            headers = [cell.lower() for cell in rows[0]]
            paid_index = next(
                (index for index, cell in enumerate(headers) if "paid tier" in cell),
                None,
            )
            measure = unit_measure(headers[paid_index])
            # A column billed in tokens prices charges, and its rows say which
            # charge; a column billed in anything else prices whole calls, and its
            # rows say which variant.
            if paid_index is None or measure is None or measure.endswith("tokens"):
                continue
            for row in rows[1:]:
                label = clean_text(row[0])
                prices = []
                for rate in cell_rates(
                    row[paid_index], header=headers[paid_index], currency="USD"
                ):
                    prices.append(
                        {
                            "conditions": (
                                {"price_scope": "；".join(rate.conditions)}
                                if rate.conditions
                                else {}
                            ),
                            "prices": [
                                price_item(
                                    "output",
                                    label,
                                    rate.amount,
                                    price_unit_code(
                                        rate.unit_phrase,
                                        headers[paid_index],
                                        currency="USD",
                                        default="USD_per_million_tokens",
                                    ),
                                    display=rate.display,
                                    list_amount=rate.list_amount,
                                    discount=rate.discount,
                                )
                            ],
                        }
                    )
                if prices:
                    collected.append((label, prices))
        if not collected:
            return {}
        return collected

    def _records(self) -> list[dict[str, Any]]:
        retrieved_at = now_iso()
        records = []
        for section in self._sections():
            model_ids = section["model_ids"]
            collected = self._variant_offers(section["tables"])
            assignment = (
                assign_variant_rows(model_ids, [label for label, _ in collected])
                if collected
                else None
            )
            if assignment:
                prices_by_label = dict(collected)
                shared = self._offers(section["tables"])
                for position, model_id in enumerate(model_ids):
                    labels = assignment.get(model_id, [])
                    offers = [
                        offer for label in labels for offer in prices_by_label[label]
                    ]
                    records.append(
                        self._record(
                            section,
                            model_id,
                            [*offers, *(shared if position == 0 else [])],
                            retrieved_at,
                            # A family split into its variants answers for one id
                            # each: carrying the whole family on every record would
                            # let any of its ids return another variant's price.
                            aliases=[model_id],
                        )
                    )
                continue
            records.append(
                self._record(
                    section, model_ids[0], self._offers(section["tables"]), retrieved_at
                )
            )
        return records

    def _record(
        self,
        section: dict[str, Any],
        model_id: str,
        offers: list[dict[str, Any]],
        retrieved_at: str,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        return make_record(
            self.provider_id,
            self.provider_name,
            model_id,
            section["display_name"],
            "全球",
            offers,
            self.source_url,
            self.source_kind,
            retrieved_at,
            currency="USD",
            delivery_mode="first_party",
            model_family=model_family(model_id),
            model_aliases=aliases or section["model_ids"],
            source_api=GEMINI_MARKDOWN_URL,
        )

    def list_models(self, prefix: str = "") -> list[str]:
        models = {
            model_id
            for section in self._sections()
            for model_id in section["model_ids"]
        }
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models, key=str.lower)

    def catalog_records(self) -> list[dict[str, Any]]:
        """Every model section of the Gemini document is read in one pass."""
        return self._records()

    def query(self, model: str) -> list[dict[str, Any]]:
        key = normalize_model(model)
        return [
            record
            for record in self._records()
            if any(normalize_model(alias) == key for alias in record["model_aliases"])
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        """Match a section once, even when several API ids resolve to it."""
        return [
            record
            for record in self._records()
            if any(
                model_matches(model, alias, exact=exact)
                for alias in record["model_aliases"]
            )
        ]
