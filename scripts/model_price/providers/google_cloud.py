"""Google Cloud's per-model prices for the models it serves itself.

The page renders one table per model family per delivery tier, with every tier —
Standard, Priority, Flex/Batch, Deferred — and every regional group already in the
served HTML, so one request covers the catalogue. Three things about the page have
to be said before its tables can be read:

A delivery tier is a *tab*, not a heading, so several tiers' tables would otherwise
be filed under one section and merged. Each panel is rewritten as a heading that
says it came from a tab, which is how a tier reaches a price row as its billing
tier while the family heading above it does not.

A cell that stacks two values writes each in its own paragraph ("Gemini 2.5 Pro"
over "Computer Use-Preview"), and the shared reader knows one line break. The
paragraph boundary is spelled as that break, without which the two values arrive
glued into a model id the page never published.

Prices arrive in columns that name their own unit or state none at all. A column
that names none keeps the amount as the page published it rather than being read as
a token rate the page never put there: this page quotes thousand-call counts,
modality-hours, storage hours and per-picture rates beside its token rates.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..models import normalize_model
from ..parsing import price_unit_code
from ..pricing import per_million_tokens
from .base import CellRate, TabularPricingAdapter

GOOGLE_CLOUD_PRICING_URL = (
    "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing"
)
# The schedule the same platform publishes beside its prices: the literal model id,
# its release date, the day it retires and what replaces it.
GOOGLE_CLOUD_MODEL_VERSIONS_URL = (
    "https://cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions"
)

# A tab panel becomes a heading carrying this marker. The tier is what the panel's
# tab named, and no other heading on the page is a tier: "Gemini 3" is a family the
# tables below it belong to, while "Flex/Batch" is what a rate is billed at.
GOOGLE_TAB_MARKER = "pricing tab:"
# The key the page's Region column publishes its values under, reused for the region
# a tab selects so one fact keeps one name.
GOOGLE_REGION_CONDITION = "Region"
GOOGLE_TAB_RE = re.compile(r"<button\b[^>]*\brole=\"tab\"[^>]*>", re.I)
GOOGLE_TAB_ID_RE = re.compile(r"\bid=\"([^\"]+)\"")
GOOGLE_TAB_LABEL_RE = re.compile(r"\btrack-metadata-eventdetail=\"([^\"]*)\"")
GOOGLE_PANEL_RE = re.compile(r"<div\b[^>]*\brole=\"tabpanel\"[^>]*>", re.I)
GOOGLE_PANEL_LABEL_RE = re.compile(r"\baria-labelledby=\"([^\"]+)\"")
GOOGLE_PARAGRAPH_BREAK_RE = re.compile(r"</p>\s*<p[^>]*>", re.I)

# Cache storage is billed per token per hour, and the section that publishes it says
# so in the column header ("Price Tok/hr<= 200K input tokens") rather than beside
# the figure. The figure is therefore a rate per token-hour, and it is reported in
# the unit the same vendor uses for cache storage elsewhere — per million tokens per
# hour — rather than per hour, which would understate it a millionfold.
GOOGLE_TOKEN_HOUR_MARKERS = ("tok/hr", "tok-hour", "token/hour", "token-hour")
GOOGLE_TOKEN_HOUR_UNIT = "1mtoken/hour"
GOOGLE_STORAGE_MARKERS = ("storage", "modality-hour", "tok-hour", "tok/hr")

# "Input" and "Output" head the modality a media row bills for, and a charge on no
# table here. Read as a charge they are dropped from the conditions, and the price
# beside them loses the modality it is for. A table that does put money in such a
# column still has it read: with no charge named by the header, the shared reader
# classifies the column by the amounts it publishes.
GOOGLE_DIRECTION_HEADERS = frozenset({"input", "output"})

# A tab names either the service level a rate is billed at or the place it runs in,
# and the page gives both selectors the same markup. A place is what this vendor's
# own region list says it is: its regional scope ("Global"), one of its multi-region
# groups, or a region id, which Google writes as a location followed by a number
# ("us-east5", "asia-southeast1") — spelled as the page spells it, spaces and all
# ("europe-west 1"). The two selectors must not be merged: filed as one tier, a
# region would make seven offers of one rate that differ by nothing but where it
# runs, and the region itself would be dropped from the row that states it.
GOOGLE_REGION_TAB_RE = re.compile(
    r"^(?:global|.*multi-region.*|[a-z]+(?:-[a-z]+)+\s?\d+)$", re.I
)

# The page's own names for the tier a table prices at, mapped onto the names the
# report ranks offers by. Two sections are filed under a *pricing scheme* rather
# than a tier ("Token-based pricing" beside "Modality-based pricing" for the same
# models), and a scheme is the page's ordinary rate. A tier word this tool has not
# seen keeps the page's own wording, which ranks it below the standard rate — the
# safe reading of a tier whose standing this tool cannot tell.
GOOGLE_TIER_MARKERS = (
    ("priority", "priority"),
    ("flex", "batch"),
    ("batch", "batch"),
)
GOOGLE_STANDARD_TIER_MARKERS = ("standard", "token-based", "modality-based")

# A column whose header names tokens or a per-million scale prices tokens even when
# it writes the unit only in the neighbouring table ("Token Price <= 200K tokens").
GOOGLE_TOKEN_MARKERS = ("token", "/1m", "per 1m", "1,000,000")


def google_region_tab(tab: str) -> bool:
    """Whether a tab names where a rate runs rather than what service level it is."""
    return bool(GOOGLE_REGION_TAB_RE.match(tab.strip()))


def google_tier_name(tier: str) -> str:
    """The stable name of the delivery tier a tab published, or ``""``."""
    lowered = tier.strip().lower()
    if not lowered:
        return ""
    if any(marker in lowered for marker in GOOGLE_STANDARD_TIER_MARKERS):
        return "standard"
    for marker, name in GOOGLE_TIER_MARKERS:
        if marker in lowered:
            return name
    return normalize_model(tier)


def google_prices_tokens(header: str) -> bool:
    """Whether a column prices tokens, even where it states no scale."""
    return any(marker in header.lower() for marker in GOOGLE_TOKEN_MARKERS)


def google_cloud_tab_labels(document: str) -> dict[str, str]:
    """Map each tab panel's id to the label of the tab that opens it."""
    labels: dict[str, str] = {}
    for tag in GOOGLE_TAB_RE.findall(document):
        identifier = GOOGLE_TAB_ID_RE.search(tag)
        label = GOOGLE_TAB_LABEL_RE.search(tag)
        if identifier and label and label.group(1).strip():
            labels[identifier.group(1)] = label.group(1).strip()
    return labels


def google_cloud_document(document: str) -> str:
    """Rewrite the served page so the shared table reader keeps its distinctions."""
    labels = google_cloud_tab_labels(document)

    def heading(match: re.Match[str]) -> str:
        target = GOOGLE_PANEL_LABEL_RE.search(match.group(0))
        label = labels.get(target.group(1), "") if target else ""
        if not label:
            return "<div>"
        return f"<h3>{html.escape(f'{GOOGLE_TAB_MARKER}{label}')}</h3>"

    page = GOOGLE_PARAGRAPH_BREAK_RE.sub("</p><br><p>", document)
    return GOOGLE_PANEL_RE.sub(heading, page)


def google_tab_tier(headings: list[str]) -> str:
    """The delivery tier the tab above a table named, or ``""`` when none did."""
    marker = GOOGLE_TAB_MARKER
    return next(
        (
            heading[len(marker) :].strip()
            for heading in reversed(headings)
            if heading.startswith(marker)
        ),
        "",
    )


def google_price_kind(header: str) -> str | None:
    """The charge one of this page's headers names, or ``None`` for a condition."""
    lowered = header.lower()
    if lowered.strip() in GOOGLE_DIRECTION_HEADERS:
        return None
    if any(marker in lowered for marker in GOOGLE_STORAGE_MARKERS):
        return "cache_storage"
    return None


def google_token_hour_rate(header: str) -> bool:
    """Whether a column prices its figures per token per hour."""
    lowered = header.lower()
    return any(marker in lowered for marker in GOOGLE_TOKEN_HOUR_MARKERS)


class GoogleCloudAdapter(TabularPricingAdapter):
    provider_id = "google-cloud"
    provider_name = "Google Cloud Vertex AI"
    source_url = GOOGLE_CLOUD_PRICING_URL
    source_kind = "official_html"
    currency = "USD"
    region = "全球"
    # Every row after the first of a model's group leaves the model cell empty.
    carry_forward_model = True

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._document: str | None = None

    def document_text(self) -> str:
        """Read the served page once, with its tabs and paragraphs made readable."""
        if self._document is None:
            self._document = google_cloud_document(
                self.document(GOOGLE_CLOUD_PRICING_URL)
            )
        return self._document

    def model_column(self, headers: list[str]) -> int | None:
        """Locate the model column, including the ones a section renamed.

        Most tables head it "Model"; the open-source table heads it "Open Source
        Model" and the AlphaEvolve table "Gemini model". A header naming a model
        that prices nothing is that column, wherever it sits.
        """
        explicit = super().model_column(headers)
        if explicit is not None:
            return explicit
        return next(
            (
                index
                for index, header in enumerate(headers)
                if header.lower().endswith("model") and self.price_kind(header) is None
            ),
            None,
        )

    def model_variants(self, display_name: str, note: str = "") -> list[str]:
        """Read a cell that names several models as the models it names.

        Cache storage prices one rate against a list of models in one cell
        ("Gemini 3.8 Flash Cyber, Gemini 3.8 Flash, Gemini ..."), and each of them
        is charged that rate.
        """
        if "," not in display_name:
            return super().model_variants(display_name, note)
        return [part.strip() for part in display_name.split(",") if part.strip()]

    def price_kind(self, header: str) -> str | None:
        """Read the shared price roles, then this page's own two exceptions."""
        if header.strip().lower() in GOOGLE_DIRECTION_HEADERS:
            return None
        return google_price_kind(header) or super().price_kind(header)

    def heading_conditions(self, headings: list[str]) -> dict[str, Any]:
        tab = google_tab_tier(headings)
        if not tab:
            return {}
        if google_region_tab(tab):
            # Filed under the same key the page's own Region column uses, because it
            # is the same fact: where the rate runs.
            return {GOOGLE_REGION_CONDITION: tab}
        tier = google_tier_name(tab)
        return {"service_tier": tier} if tier else {}

    def offer_name(self, headings: list[str]) -> str:
        # The family a table belongs to already travels as ``source_section``; the
        # offer is named by the tier its tab published, when it published a tier.
        tab = google_tab_tier(headings)
        if not tab or google_region_tab(tab):
            return self.default_offer_name
        return google_tier_name(tab) or self.default_offer_name

    def row_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        """Take this adapter's own tab marker back off a row it filed.

        The marker is how a tier is told from a family heading while the page is
        read, and it is this adapter's writing rather than the vendor's. Where it
        was all the section held, the fact it named already travels as a condition
        of its own — the tier, or the region.
        """
        section = str(conditions.get("source_section") or "")
        if not section.startswith(GOOGLE_TAB_MARKER):
            return conditions
        return {
            key: value
            for key, value in conditions.items()
            if key != "source_section"
        }

    def cell_rates(self, cell: str, header: str) -> list[CellRate]:
        """Read a cell, stating a per-token-hour rate in the unit it is billed in."""
        rates = super().cell_rates(cell, header)
        if not google_token_hour_rate(header):
            return rates
        return [
            rate._replace(
                amount=per_million_tokens(rate.amount, 1),
                unit_phrase=GOOGLE_TOKEN_HOUR_UNIT,
            )
            for rate in rates
        ]

    def price_unit(self, header: str, cell_unit: str = "") -> str:
        """The unit a column prices in, or none when its column states none.

        A column that names tokens prices tokens even where it leaves the scale to
        the neighbouring table, and one that names neither tokens nor any other unit
        this tool reads keeps its amount as the page published it — thousand-call
        counts and per-picture rates live in columns no token default would fit.
        """
        code = price_unit_code(
            cell_unit, header, currency=self.currency, default=""
        )
        if code:
            return code
        return "USD_per_million_tokens" if google_prices_tokens(header) else "provider_defined"
