"""Microsoft Azure Foundry's price pages, and the models it serves from them.

Azure publishes these prices on surfaces shaped differently from each other and
from every other vendor here:

- the Foundry Models pages, one per model vendor, whose amounts sit in a per-region
  JSON attribute on a ``$-`` placeholder that the browser fills in — a cell of
  placeholders publishes no amount at all, so the figure a region yields has to be
  written back where the placeholder was;
- the Azure OpenAI page, whose amounts are the same placeholders but whose meters
  are named inside the cell ("Input: … Cached Input: … Output: …") while the column
  names the pricing plan;
- and Claude, which Azure does not price at all. Anthropic publishes the rate a
  Foundry deployment is billed at — its own standard rate — and the multiplier a US
  Data Zone deployment adds to it, so the catalogue comes from the Foundry page and
  the rates from the Anthropic page that states them.

One region is read for every amount, the American one Azure lists first, because a
page prices the same model in thirty-odd regions and the rate a reader means is the
American one. The Foundry Models landing page names its sibling pages in a tab
strip, so the serverless catalogue is discovered from that strip rather than listed
here: the set of vendors Azure prices changes, and a page this tool did not know
about would be a catalogue it silently did not cover.

The pages state their amounts in the region's own currency without naming it, and
every region this adapter reads is American, so the currency is the dollar the
pricing page is published in.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from ..models import normalize_model
from ..parsing import markdown_tables, price_unit_code
from ..text import clean_text
from .anthropic import ANTHROPIC_MARKDOWN_URL, anthropic_price_rows
from .base import CellRate, TabularPricingAdapter

AZURE_OPENAI_URL = "https://azure.microsoft.com/en-us/pricing/details/azure-openai/"
AZURE_FOUNDRY_MODELS_URL = (
    "https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models"
)
AZURE_FOUNDRY_TABS_URL = f"{AZURE_FOUNDRY_MODELS_URL}/aoai/"
ANTHROPIC_FOUNDRY_MARKDOWN_URL = (
    "https://platform.claude.com/docs/en/build-with-claude/claude-in-microsoft-foundry.md"
)
# The schedule Microsoft publishes for its own models: the literal model name, the
# version that pins a deployment, and the day its deployments stop.
AZURE_MODEL_RETIREMENTS_URL = (
    "https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements"
)

# The region every amount of every page is read for: East US, the region Azure lists
# its American prices first, and the first American region a page names when it does
# not price East US at all.
AZURE_DEFAULT_REGION = "us-east"
AZURE_US_PREFIX = "us-"

# The pages spell the model column "Model" and "Models" both.
AZURE_MODEL_HEADERS = frozenset({"model", "models"})

# A column headed with the charge alone ("Price") still prices one: the unit is in
# its cells ("N/A/hour", "$15 /1M characters") or in the page's prose. Reading the
# column is what keeps a model the page lists without quoting a figure in the
# catalogue, where its listing and its later price stay two separate facts.
AZURE_PRICE_HEADERS = frozenset({"price"})

# A column that prices reserved capacity rather than use of the model. Azure names
# the unit of such a column after the product it sells ("Per PTU Hourly pricing",
# "Per PTU Monthly Reservation Pricing", "Price (Unit/Hour) Monthly Commit"), and
# capacity is not what this tool compares: read as a use price, a monthly reservation
# would be quoted as the cost of a request.
AZURE_CAPACITY_MARKERS = ("ptu", "reservation", "commit", "provisioned")

# What the last words of a model cell say about a deployment rather than about the
# model: where it runs (Global, US/EU Data Zones, Regional, Developer) and which
# context length it bills. Both travel as conditions of the price — one model is
# deployed both ways, and folded into the id they would read as separate models.
AZURE_CELL_QUALIFIERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\s*(?:US/EU\s*[–—-]?\s*)?Data\s?Zones?\s*$", re.I),
        "deployment_scope",
    ),
    (re.compile(r"\s*Global\s*$", re.I), "deployment_scope"),
    (re.compile(r"\s*Regional\s*$", re.I), "deployment_scope"),
    (re.compile(r"\s*Developer\s*$", re.I), "deployment_scope"),
    (re.compile(r"\s*Long\s+Context\s*$", re.I), "context_tier"),
)

# One amount of one page: the element carrying the per-region figures, with the
# placeholder the browser replaces inside it.
AZURE_PRICE_SPAN_RE = re.compile(
    r"<span(?P<attrs>[^>]*?\bdata-amount='(?P<amount>\{[^']*\})'[^>]*)>"
    r"<span[^>]*class=['\"]price-value['\"][^>]*>(?P<placeholder>[^<]*)</span></span>",
    re.S | re.I,
)
AZURE_DECIMALS_RE = re.compile(r"\bdata-decimals=\"(?P<decimals>\d+)\"")
AZURE_UNAVAILABLE_RE = re.compile(r"\bdata-region-unavailable=\"(?P<text>[^\"]*)\"")
AZURE_TABLE_RE = re.compile(r"<table(?P<attrs>[^>]*)>", re.I)
AZURE_TABLE_LABEL_RE = re.compile(r"\baria-label=\"(?P<label>[^\"]*)\"", re.I)
AZURE_THEAD_RE = re.compile(r"<thead\b[^>]*>(?P<body>.*?)</thead>", re.S | re.I)
AZURE_HEADER_ROWSPAN_RE = re.compile(r'(<th\b[^>]*?)\s*rowspan="\d+"', re.I)
# For ``re.sub``: keep the opening part of a header cell and drop its span.
KEEP_FIRST_GROUP = "\\1"
AZURE_BRACKETED_RE = re.compile(r"[（(][^（()）]*[)）]")
AZURE_FOUNDRY_PAGE_RE = re.compile(
    r'href="/en-us/pricing/details/ai-foundry-models/(?P<slug>[a-z0-9-]+)/"'
)

# The multiplier Anthropic publishes for a Foundry deployment of a US Data Zone.
AZURE_MULTIPLIER_RE = re.compile(r"(?P<rate>\d+(?:\.\d+)?)\s*x\s+multiplier", re.I)


def azure_payloads(document: str) -> Iterator[dict[str, Any]]:
    """Every per-region amount payload one page publishes."""
    for match in AZURE_PRICE_SPAN_RE.finditer(document):
        try:
            payload = json.loads(match.group("amount"), parse_float=str)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def azure_region(document: str) -> str:
    """The region every amount of one page is read for."""
    payloads = list(azure_payloads(document))
    for payload in payloads:
        if AZURE_DEFAULT_REGION in (payload.get("regional") or {}):
            return AZURE_DEFAULT_REGION
    for payload in payloads:
        for region in payload.get("regional") or {}:
            if str(region).startswith(AZURE_US_PREFIX):
                return str(region)
    return AZURE_DEFAULT_REGION


def azure_amount(value: Any, decimals: str = "") -> str:
    """One figure as the page would show it, at the precision it publishes.

    The page states how many decimals to show; the figures themselves are
    machine-written and carry more of them, including the trailing noise of a binary
    decimal ("260.00000000000000000000000003"). Rounding to the stated precision and
    dropping the zeros that leaves behind is what the browser does with them.
    """
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if decimals:
        number = number.quantize(Decimal(1).scaleb(-int(decimals)))
    return format(number.normalize(), "f")


def azure_currency(placeholder: str) -> str:
    """The currency the page prints in front of its placeholder.

    The page writes an amount as a currency sign and a dash ("$-") and replaces the
    figure in the browser, so the sign is the only statement of what the amount is in.
    Kept, because an amount that names no currency is not a price this tool may read.
    """
    return placeholder.split("-", 1)[0].strip()


def azure_figure(
    payload: dict[str, Any], attrs: str, region: str, placeholder: str = "$-"
) -> str:
    """The figure one payload yields for one region, or what the page shows instead."""
    unavailable = AZURE_UNAVAILABLE_RE.search(attrs)
    fallback = unavailable.group("text") if unavailable else "N/A"
    decimals = AZURE_DECIMALS_RE.search(attrs)
    regional = payload.get("regional") or {}
    value = (
        regional.get(region) if regional else (payload.get("currencies") or {}).get("USD")
    )
    if value is None:
        return fallback
    figure = azure_amount(value, decimals.group("decimals") if decimals else "")
    return f"{azure_currency(placeholder)}{figure}"


def azure_document(document: str, region: str) -> str:
    """Write the amount one region publishes where the page shows a placeholder.

    A page served without a browser carries no figures at all, so this is what makes
    its tables readable: the region is chosen once and every amount is that region's.
    """

    def replace(match: re.Match[str]) -> str:
        try:
            payload = json.loads(match.group("amount"), parse_float=str)
        except json.JSONDecodeError:
            return match.group(0)
        if not isinstance(payload, dict):
            return match.group(0)
        return azure_figure(
            payload, match.group("attrs"), region, match.group("placeholder")
        )

    return AZURE_PRICE_SPAN_RE.sub(replace, document)


def azure_headings(document: str) -> str:
    """Name each table after its own label, so a table keeps the section it is in.

    These pages give each table an ``aria-label`` naming the family it prices ("GPT-6
    Series", "Language models") and put nothing above it that a heading reader sees.
    Written as the heading the shared reader looks for, that family reaches the record
    as the section its rates were published under.
    """

    def replace(match: re.Match[str]) -> str:
        label = AZURE_TABLE_LABEL_RE.search(match.group("attrs") or "")
        if not label or not label.group("label").strip():
            return match.group(0)
        return f"<h3>{label.group('label').strip()}</h3>{match.group(0)}"

    return AZURE_TABLE_RE.sub(replace, document)


def azure_header_spans(document: str) -> str:
    """Drop a header row's own rowspan where the table never uses a second one.

    One table declares its model header ``rowspan="2"`` and publishes a single header
    row. The shared reader carries a spanned cell into every row it covers — which is
    what keeps a vendor's merged cells from shifting a table's columns — so the header
    would land on the table's first model and push that model's name into its own
    price column, leaving a model called "Models" behind. Where a header row is the
    table's only one, the span covers nothing and is dropped.
    """

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        if len(re.findall(r"<tr\b", body, re.I)) != 1:
            return match.group(0)
        opening = match.group(0).split(">", 1)[0]
        return f"{opening}>{AZURE_HEADER_ROWSPAN_RE.sub(KEEP_FIRST_GROUP, body)}</thead>"

    return AZURE_THEAD_RE.sub(replace, document)


def azure_foundry_pages(document: str) -> list[str]:
    """Every serverless price page the landing page's tab strip names."""
    return [
        f"{AZURE_FOUNDRY_MODELS_URL}/{slug}/"
        for slug in dict.fromkeys(AZURE_FOUNDRY_PAGE_RE.findall(document))
    ]


def azure_price_kind(header: str) -> str | None:
    """The charge a column heads, where the page heads it with the charge alone."""
    name = AZURE_BRACKETED_RE.sub("", header).strip().lower()
    return "price" if name in AZURE_PRICE_HEADERS else None


def azure_prices_capacity(header: str) -> bool:
    """Whether a column prices reserved capacity rather than use of the model."""
    lowered = header.lower()
    return any(marker in lowered for marker in AZURE_CAPACITY_MARKERS)


def azure_cell_parts(value: str) -> tuple[str, dict[str, str]]:
    """Split a model cell into the model's name and what the cell says about it."""
    name = clean_text(value)
    conditions: dict[str, str] = {}
    stripped = True
    while stripped and name:
        stripped = False
        for pattern, key in AZURE_CELL_QUALIFIERS:
            match = pattern.search(name)
            if match and match.start() > 0:
                conditions.setdefault(key, clean_text(match.group(0)))
                name = name[: match.start()].strip().strip(" -–—")
                stripped = True
                break
    return name, conditions


def anthropic_foundry_multiplier(markdown: str) -> str:
    """The multiplier Anthropic publishes for a US Data Zone Foundry deployment.

    Read from the sentence that states it, and only from one that also names the
    Foundry: the same page states multipliers for other platforms, and applying
    another platform's would price Azure at a rate nobody published for it.
    """
    for sentence in re.split(r"(?<=[.!?])\s+", markdown):
        if "foundry" not in sentence.lower():
            continue
        match = AZURE_MULTIPLIER_RE.search(sentence)
        if match:
            return match.group("rate")
    return ""


def azure_premium_price(price: dict[str, Any], multiplier: str) -> dict[str, Any]:
    """One rate as a deployment that costs the published multiplier more.

    The amount is the marked-up rate and ``list_amount`` is the rate it is a premium
    on, both as the vendor published them: the multiplier is the vendor's own, so the
    product is its stated rule rather than a rate derived from two others.
    """
    try:
        marked = Decimal(str(price.get("amount"))) * Decimal(multiplier)
    except (InvalidOperation, TypeError, ValueError):
        return dict(price)
    premium = dict(price)
    premium["amount"] = format(marked.normalize(), "f")
    premium["list_amount"] = price.get("amount")
    premium["discount"] = multiplier
    return premium


class AzureAdapter(TabularPricingAdapter):
    provider_id = "azure"
    provider_name = "Microsoft Azure Foundry"
    source_url = AZURE_OPENAI_URL
    catalog_url = AZURE_FOUNDRY_MODELS_URL
    source_kind = "official_html"
    currency = "USD"
    region = AZURE_DEFAULT_REGION
    delivery_mode = "platform_hosted"
    # Every row after the first of a model's group leaves the model cell empty.
    carry_forward_model = True

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._documents: tuple[tuple[str, str], ...] | None = None
        self._standard_rates: dict[str, list[dict[str, Any]]] | None = None

    def source_documents(self) -> tuple[tuple[str, str], ...]:
        """Read every page this provider prices from, once each.

        The region is taken from the OpenAI page and every other page is read for the
        same one, so a scan reports one region's prices rather than a mix of them, and
        the record says which region that was.
        """
        if self._documents is not None:
            return self._documents
        openai_page = self.document(AZURE_OPENAI_URL)
        self.region = azure_region(openai_page)
        documents: list[tuple[str, str]] = [
            (AZURE_OPENAI_URL, azure_page(openai_page, self.region))
        ]
        tabs_page = self.document(AZURE_FOUNDRY_TABS_URL)
        for url in azure_foundry_pages(tabs_page):
            if url != AZURE_FOUNDRY_TABS_URL:
                documents.append((url, azure_page(self.document(url), self.region)))
        documents.append(
            (
                ANTHROPIC_FOUNDRY_MARKDOWN_URL,
                self.document(ANTHROPIC_FOUNDRY_MARKDOWN_URL),
            )
        )
        self._documents = tuple(documents)
        return self._documents

    def _document_rows(self, text: str, source_url: str) -> list[dict[str, Any]]:
        """Read one page, leaving the vendor-markdown catalogue to its own reader."""
        if source_url == ANTHROPIC_FOUNDRY_MARKDOWN_URL:
            return self._claude_rows(text)
        return super()._document_rows(text, source_url)

    def model_column(self, headers: list[str]) -> int | None:
        """Locate the model column, spelled either way these pages spell it."""
        return next(
            (
                index
                for index, header in enumerate(headers)
                if header.strip().lower() in AZURE_MODEL_HEADERS
            ),
            None,
        )

    def price_kind(self, header: str) -> str | None:
        """Read the shared price roles, and the columns headed with the charge alone."""
        if azure_prices_capacity(header):
            return "price"
        return super().price_kind(header) or azure_price_kind(header)

    def cell_rates(self, cell: str, header: str) -> list[CellRate]:
        """Read a cell's rates, leaving reserved capacity to the page it is on."""
        if azure_prices_capacity(header):
            return []
        return super().cell_rates(cell, header)

    def model_conditions(self, display_name: str) -> dict[str, Any]:
        return azure_cell_parts(display_name)[1]

    def model_variants(self, display_name: str, note: str = "") -> list[str]:
        name, _ = azure_cell_parts(display_name)
        return [name] if name else []

    def row_offer_name(self, headings: list[str], conditions: dict[str, Any]) -> str:
        return str(conditions.get("deployment_scope") or self.default_offer_name)

    def price_unit(self, header: str, cell_unit: str = "") -> str:
        """The unit a column prices in, or none when its column states none."""
        return price_unit_code(
            cell_unit, header, currency=self.currency, default="provider_defined"
        )

    def _claude_rows(self, markdown: str) -> list[dict[str, Any]]:
        """Claude's Foundry models, at the rates Anthropic publishes for them.

        The Foundry page names each model and the deployment name Azure gives it, and
        states that a deployment is billed at Anthropic's own rates. Those rates are
        read from the Anthropic page that publishes them rather than restated here,
        and the US Data Zone deployment type Azure adds — the published multiplier
        more — is a second offer, so the premium appears as the term the vendor
        stated rather than folded into one figure.

        The model keeps the name it is published under rather than the deployment name
        beside it: a deployment name is what one subscription calls its deployment,
        and this one spells the version out in dashes ("claude-opus-5-5") where the
        model's own id carries a dot — reading it as the identity would leave a query
        for the model finding nothing.
        """
        catalogue = next(
            (
                rows
                for _, rows in markdown_tables(markdown)
                if rows and any("deployment name" in cell.lower() for cell in rows[0])
            ),
            [],
        )
        if len(catalogue) < 2:
            return []
        pricing = self.document(ANTHROPIC_MARKDOWN_URL)
        rates = self._anthropic_rates(pricing)
        multiplier = anthropic_foundry_multiplier(pricing)
        rows: list[dict[str, Any]] = []
        for cells in catalogue[1:]:
            cells += [""] * (4 - len(cells))
            display_name = clean_text(
                re.sub(r"\[([^\]]*)]\([^)]*\)", r"\1", cells[0])
            )
            deployment = clean_text(cells[1]).strip("`")
            prices = rates.get(normalize_model(display_name)) or []
            if not display_name or not deployment or not prices:
                continue
            hosting = [
                label
                for label, cell in (
                    ("Hosted on Azure", cells[2]),
                    ("Hosted on Anthropic", cells[3]),
                )
                if clean_text(cell)
            ]
            conditions: dict[str, Any] = {
                "billing_mode": "pay_as_you_go",
                "deployment_name": deployment,
            }
            if hosting:
                conditions["hosting"] = "、".join(hosting)
            for scope, offer_prices in (
                ("Global Standard", prices),
                (
                    "US Data Zone Standard",
                    [azure_premium_price(price, multiplier) for price in prices],
                )
                if multiplier
                else ("", []),
            ):
                if not offer_prices:
                    continue
                rows.append(
                    {
                        "model_id": normalize_model(display_name),
                        "display_name": display_name,
                        "offer_name": normalize_model(scope),
                        "conditions": {**conditions, "deployment_scope": scope},
                        "prices": offer_prices,
                        "source_url": ANTHROPIC_FOUNDRY_MARKDOWN_URL,
                    }
                )
        return rows

    def _anthropic_rates(
        self, pricing_markdown: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Anthropic's standard rates, keyed by the model each row prices."""
        if self._standard_rates is None:
            self._standard_rates = {
                row["model_id"]: row["offer"]["prices"]
                for row in anthropic_price_rows(self.client)
                if row["offer"]["name"] == "standard"
            }
        return self._standard_rates


def azure_page(document: str, region: str) -> str:
    """One page as the shared reader needs it: labelled tables, one region's figures."""
    return azure_document(azure_headings(azure_header_spans(document)), region)
