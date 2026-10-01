"""Official model-description sources, independent from price adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

from ..deepseek_updates import (
    DEEPSEEK_UPDATES_URL,
    UpdateEntry,
    entry_for_model,
    entry_lifecycle,
    entry_model_label,
    read_updates,
)
from ..errors import SourceError
from ..models import normalize_model
from ..parsing import headed_document_tables, markdown_tables
from ..providers.aliyun import (
    ALIYUN_MODELS_URL,
    qianwen_catalogue,
    qianwen_model_metadata,
)
from ..providers.openrouter import (
    OPENROUTER_MODELS_URL,
    OPENROUTER_SITE_URL,
    openrouter_entries,
    openrouter_model_id,
    openrouter_pricing_state,
    openrouter_video_entries,
)
from ..text import CELL_BREAK_RE, clean_text
from .core import (
    ACTIVE,
    LEGACY,
    UNKNOWN,
    DescriptionSource,
    description_record,
)
from .parsing import (
    lifecycle_from_text,
    markdown_capabilities,
    markdown_specifications,
    markdown_summary,
    markdown_text,
    meta_tags,
    model_link,
    model_mentioned,
    section,
)
from .tencent_mirror import validate_tencent_mirror

OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models/all"
ANTHROPIC_MODELS_URL = "https://platform.claude.com/docs/en/models/overview"
ANTHROPIC_MODELS_MARKDOWN_URL = f"{ANTHROPIC_MODELS_URL}.md"
GEMINI_MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
XAI_MODELS_URL = "https://docs.x.ai/developers/models"
KIMI_MODELS_URL = "https://platform.kimi.com/docs/models.md"
MINIMAX_MODELS_URL = "https://platform.minimax.cn/docs/guides/models-intro.md"
ZHIPU_MODELS_URL = "https://docs.bigmodel.cn/cn/guide/start/model-overview.md"
XIAOMI_MODELS_URL = "https://mimo.mi.com/docs/zh-CN/quick-start/summary/model"
KLING_VIDEO_CAPABILITY_URL = (
    "https://klingai.com/document-api/guides/capability-map/video.md"
)
KLING_IMAGE_CAPABILITY_URL = (
    "https://klingai.com/document-api/guides/capability-map/image.md"
)
XIAOMI_MODEL_ID = re.compile(
    r"(?<![\w.-])[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)+(?![\w.-])"
)
VOLCENGINE_MODELS_URL = "https://console.volcengine.com/ark/region:cn-beijing/model"


def _not_found_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "404" in text or "not found" in text


class MarkdownDetailSource(DescriptionSource):
    """Fetch one official Markdown model page derived from the model id."""

    url_for: Callable[[str], str]

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._describe_url(
            self.url_for(model_id),
            model_id,
            display_name,
        )

    def _describe_url(
        self,
        url: str,
        model_id: str,
        display_name: str = "",
        *,
        authoritative: bool = False,
    ) -> dict[str, Any] | None:
        """Read one detail URL, optionally requiring an index-confirmed page.

        Generated URLs can honestly miss when a vendor has no independent page,
        so their 404 remains ``not_found``. A URL copied from an official index is
        different: if that page disappears or no longer describes the indexed
        model, the source is inconsistent and the resolver must report an error.
        """
        try:
            text = self.client.get_text(url)
        except SourceError as exc:
            if _not_found_error(exc) and not authoritative:
                return None
            raise
        # Several documentation sites answer an unknown path with a soft 200.
        # A page must name the requested model before its prose is trusted.
        if not model_mentioned(text, model_id, display_name):
            if authoritative:
                raise SourceError(
                    f"official model page did not name the indexed model: {url}"
                )
            return None
        summary = markdown_summary(text)
        if not summary:
            if authoritative:
                raise SourceError(
                    f"official model page published no readable summary: {url}"
                )
            return None
        # A feature can be in preview while the model itself is stable, so
        # lifecycle is read only from a dedicated availability section, never
        # from the whole page.
        lifecycle = lifecycle_from_text(section(text, "Availability"))
        if lifecycle == UNKNOWN:
            lifecycle = ACTIVE
        return description_record(
            model_id,
            display_name or model_id,
            summary,
            url.removesuffix(".md").removesuffix(".md.txt"),
            self.source_kind,
            source_name=self.source_name,
            capabilities=markdown_capabilities(text),
            lifecycle=lifecycle,
            specifications=markdown_specifications(text),
        )


class OpenAIDescriptionSource(MarkdownDetailSource):
    source_id = "openai"
    source_name = "OpenAI"
    source_url = OPENAI_MODELS_URL
    source_kind = "official_markdown"
    url_for = staticmethod(
        lambda model: "https://developers.openai.com/api/docs/models/"
        f"{normalize_model(model)}.md"
    )


ANTHROPIC_MODEL_LINK_RE = re.compile(r"\[([^]]+)]\(([^)]+)\)")
ANTHROPIC_MODEL_PAGE_PATH_RE = re.compile(r"/docs/en/models/([^/]+)/overview(?:\.md)?$")


def _anthropic_model_page(target: str) -> tuple[str, str] | None:
    """Validate one official index link and return its public page and slug."""
    resolved = urljoin(ANTHROPIC_MODELS_URL, target)
    parsed = urlsplit(resolved)
    match = ANTHROPIC_MODEL_PAGE_PATH_RE.fullmatch(parsed.path)
    if parsed.netloc != "platform.claude.com" or not match:
        return None
    path = parsed.path.removesuffix(".md")
    return f"https://platform.claude.com{path}", match.group(1)


def anthropic_model_pages(document: str) -> dict[str, str]:
    """Map official model labels and API ids to index-published detail pages."""
    pages: dict[str, str] = {}

    def register(value: str, page: str) -> None:
        key = normalize_model(markdown_text(value))
        if key:
            pages[key] = page

    # This also covers the prose list of legacy models below the comparison table.
    for label, target in ANTHROPIC_MODEL_LINK_RE.findall(document):
        resolved = _anthropic_model_page(target)
        if resolved is None:
            continue
        page, slug = resolved
        register(label, page)
        register(f"claude-{slug}", page)

    # Current models also publish exact pinned ids and aliases by table column.
    # Those ids can differ from the human label, so bind them to the model-page
    # row instead of reconstructing a path from punctuation in an id.
    for _, rows in markdown_tables(document):
        labelled = {
            markdown_text(row[0]).lower(): row
            for row in rows
            if row and markdown_text(row[0])
        }
        page_row = labelled.get("model page")
        if not page_row:
            continue
        column_pages = []
        for cell in page_row[1:]:
            _, target = model_link(cell)
            resolved = _anthropic_model_page(target) if target else None
            column_pages.append(resolved[0] if resolved else "")
        for label in ("claude api id", "claude api alias"):
            row = labelled.get(label) or []
            for index, cell in enumerate(row[1:]):
                if index < len(column_pages) and column_pages[index]:
                    register(cell, column_pages[index])
    return pages


class AnthropicDescriptionSource(MarkdownDetailSource):
    source_id = "anthropic"
    source_name = "Anthropic"
    source_url = ANTHROPIC_MODELS_URL
    source_kind = "official_markdown"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._pages: dict[str, str] | None = None

    def _model_pages(self) -> dict[str, str]:
        if self._pages is None:
            document = self.client.get_text(ANTHROPIC_MODELS_MARKDOWN_URL)
            pages = anthropic_model_pages(document)
            if not pages:
                raise SourceError(
                    "official Anthropic model index published no model detail links"
                )
            self._pages = pages
        return self._pages

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        pages = self._model_pages()
        page = next(
            (
                pages[key]
                for key in (
                    normalize_model(model_id),
                    normalize_model(display_name),
                )
                if key and key in pages
            ),
            None,
        )
        if page is None:
            return None
        return self._describe_url(
            f"{page}.md",
            model_id,
            display_name,
            authoritative=True,
        )


class GeminiDescriptionSource(MarkdownDetailSource):
    source_id = "google"
    source_name = "Google Gemini"
    source_url = GEMINI_MODELS_URL
    source_kind = "official_markdown"
    url_for = staticmethod(
        lambda model: "https://ai.google.dev/gemini-api/docs/models/"
        f"{normalize_model(model)}.md.txt"
    )


class XAIDescriptionSource(MarkdownDetailSource):
    source_id = "xai"
    source_name = "xAI"
    source_url = XAI_MODELS_URL
    source_kind = "official_markdown"
    url_for = staticmethod(
        lambda model: f"https://docs.x.ai/developers/models/{normalize_model(model)}.md"
    )


class MarkdownTableDescriptionSource(DescriptionSource):
    """Read model/summary tables from one official Markdown overview.

    A vendor can publish its capability map across several pages, one per modality,
    and can price its own capabilities beside its models (Kling's 扩图, 数字人). Both
    are declared here rather than parsed from a name: which pages hold the
    catalogue, and which columns of a row state what a model can do.
    """

    # Further pages the same catalogue continues on, when a vendor splits it.
    source_urls: tuple[str, ...] = ()
    # The column a source names the model in, by header wording.
    model_headers: tuple[str, ...] = ("模型", "model")
    # The columns whose values are what the model can do, by header wording. A row
    # carries figures the price and specification layers already report, so only the
    # columns a source names become capabilities.
    capability_headers: tuple[str, ...] = ()

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._entries: list[dict[str, Any]] | None = None

    @property
    def documents(self) -> tuple[str, ...]:
        return self.source_urls or (self.source_url,)

    def _catalogue(self) -> list[dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        entries: list[dict[str, Any]] = []
        for url in self.documents:
            entries.extend(self._document_entries(self.client.get_text(url), url))
        if not entries:
            raise SourceError("official model-description table was not found")
        self._entries = entries
        return entries

    def _document_entries(self, text: str, source_url: str) -> list[dict[str, Any]]:
        """Every model one document's tables describe."""
        entries: list[dict[str, Any]] = []
        for headings, table in markdown_tables(text):
            if len(table) < 2 or len(table[0]) < 2:
                continue
            headers = [markdown_text(cell).lower() for cell in table[0]]
            model_index = self._column(headers, self.model_headers)
            summary_index = self._column(
                headers, ("描述", "介绍", "特点", "description")
            )
            if model_index is None or summary_index is None:
                continue
            capabilities = [
                index
                for index, header in enumerate(headers)
                if index not in (model_index, summary_index)
                and self.capability_headers
                and any(word in header for word in self.capability_headers)
            ]
            for row in table[1:]:
                row += [""] * (len(headers) - len(row))
                name, link = model_link(row[model_index])
                summary = markdown_text(row[summary_index])
                if not name or not summary:
                    continue
                lifecycle = lifecycle_from_text(" ".join([*headings, name, summary]))
                entries.append(
                    {
                        "model_id": name,
                        "display_name": name,
                        "summary": summary,
                        "category": headings[-1] if headings else "",
                        "capabilities": [
                            f"{markdown_text(table[0][index])}："
                            f"{markdown_text(row[index])}"
                            for index in capabilities
                            if markdown_text(row[index])
                        ],
                        "lifecycle": ACTIVE if lifecycle == UNKNOWN else lifecycle,
                        "url": urljoin(source_url, link) if link else source_url,
                    }
                )
        return entries

    @staticmethod
    def _column(headers: list[str], words: tuple[str, ...]) -> int | None:
        return next(
            (
                index
                for index, header in enumerate(headers)
                if any(word in header for word in words)
            ),
            None,
        )

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        keys = {normalize_model(model_id), normalize_model(display_name)} - {""}
        entry = next(
            (
                item
                for item in self._catalogue()
                if normalize_model(item["model_id"]) in keys
            ),
            None,
        )
        if not entry:
            return None
        return description_record(
            model_id,
            entry["display_name"],
            entry["summary"],
            entry["url"],
            self.source_kind,
            source_name=self.source_name,
            capabilities=entry["capabilities"]
            or ([entry["category"]] if entry["category"] else []),
            lifecycle=entry["lifecycle"],
        )

    def catalogue_descriptions(self) -> list[dict[str, Any]]:
        """Expose the same parsed overview for discovery even without a price row."""
        return [
            description
            for entry in self._catalogue()
            if (description := self.describe(entry["model_id"])) is not None
        ]


class KimiDescriptionSource(MarkdownTableDescriptionSource):
    source_id = "kimi"
    source_name = "月之暗面 Kimi"
    source_url = KIMI_MODELS_URL
    source_kind = "official_markdown"


class MiniMaxDescriptionSource(MarkdownTableDescriptionSource):
    source_id = "minimax"
    source_name = "MiniMax"
    source_url = MINIMAX_MODELS_URL
    source_kind = "official_markdown"


class ZhipuDescriptionSource(MarkdownTableDescriptionSource):
    source_id = "zhipu"
    source_name = "智谱 BigModel"
    source_url = ZHIPU_MODELS_URL
    source_kind = "official_markdown"


class KlingDescriptionSource(MarkdownTableDescriptionSource):
    """Kling's capability maps: what each model and each capability does.

    Kling publishes no per-model page. What it publishes is one map per modality —
    every model with the vendor's own words for it, what it takes in, how long a
    clip it makes and at what resolution — and the same page's capability table
    describes the platform's own chargeable capabilities (扩图, 数字人), which the
    price page prices under exactly those names.
    """

    source_id = "kling"
    source_name = "快手可灵"
    source_url = KLING_VIDEO_CAPABILITY_URL
    source_urls = (KLING_VIDEO_CAPABILITY_URL, KLING_IMAGE_CAPABILITY_URL)
    source_kind = "official_markdown"
    model_headers = ("模型", "model", "capability")
    capability_headers = (
        "input",
        "generation range",
        "resolution",
        "输入",
        "时长",
        "分辨率",
    )


class XiaomiDescriptionSource(DescriptionSource):
    source_id = "xiaomi"
    source_name = "小米 MiMo"
    source_url = XIAOMI_MODELS_URL
    source_kind = "official_html"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._entries: dict[str, dict[str, Any]] | None = None

    def _catalogue(self) -> dict[str, dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        text = self.client.get_text(self.source_url)
        entries: dict[str, dict[str, Any]] = {}
        scenarios: dict[str, list[str]] = {}
        for headings, table in headed_document_tables(text):
            if len(table) < 2:
                continue
            headers = [clean_text(cell) for cell in table[0]]
            if headers[:2] == ["需求场景", "推荐模型"]:
                for row in table[1:]:
                    if len(row) >= 2:
                        for model in XIAOMI_MODEL_ID.findall(row[1]):
                            scenarios.setdefault(normalize_model(model), []).append(
                                clean_text(row[0])
                            )
                continue
            if not headers or "模型 ID" not in headers[0] or "能力支持" not in headers:
                continue
            ability_index = headers.index("能力支持")
            limit_index = next(
                (index for index, value in enumerate(headers) if "长度限制" in value),
                None,
            )
            category = headings[-1] if headings else ""
            for row in table[1:]:
                if len(row) <= ability_index:
                    continue
                capabilities = [
                    clean_text(value)
                    for value in CELL_BREAK_RE.split(row[ability_index])
                    if clean_text(value)
                ]
                limits = (
                    clean_text(row[limit_index])
                    if limit_index is not None and limit_index < len(row)
                    else ""
                )
                matches = list(XIAOMI_MODEL_ID.finditer(row[0]))
                for index, match in enumerate(matches):
                    model = match.group()
                    next_start = (
                        matches[index + 1].start()
                        if index + 1 < len(matches)
                        else len(row[0])
                    )
                    suffix = row[0][match.end() : next_start]
                    entries[normalize_model(model)] = {
                        "model": model,
                        "capabilities": capabilities,
                        "limits": limits,
                        "category": category,
                        "lifecycle": LEGACY if "即将下线" in suffix else ACTIVE,
                    }
        for key, values in scenarios.items():
            if key in entries:
                entries[key]["scenarios"] = values
        if not entries:
            raise SourceError("official Xiaomi model table was not found")
        self._entries = entries
        return entries

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        entry = self._catalogue().get(normalize_model(model_id))
        if not entry:
            return None
        scenarios = entry.get("scenarios") or []
        summary = (
            "、".join(scenarios) if scenarios else "、".join(entry["capabilities"])
        )
        if entry["limits"]:
            summary = f"{summary}；{entry['limits']}" if summary else entry["limits"]
        return description_record(
            model_id,
            entry["model"],
            summary,
            self.source_url,
            self.source_kind,
            source_name=self.source_name,
            capabilities=[entry["category"], *entry["capabilities"]],
            lifecycle=entry["lifecycle"],
        )

    def catalogue_descriptions(self) -> list[dict[str, Any]]:
        """Use the existing table reader for both introductions and discovery."""
        return [
            description
            for entry in self._catalogue().values()
            if (description := self.describe(entry["model"])) is not None
        ]


class DeepSeekDescriptionSource(DescriptionSource):
    source_id = "deepseek"
    source_name = "DeepSeek"
    source_url = DEEPSEEK_UPDATES_URL
    source_kind = "official_document"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._entries: list[UpdateEntry] | None = None

    def entries(self) -> list[UpdateEntry]:
        """The update log, read once: every model this source describes is in it."""
        if self._entries is None:
            self._entries = read_updates(self.client)
        return self._entries

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Introduce a model from the dated release entry that announced it.

        The vendor publishes one log rather than a page per model, so the entry is
        found by the name its own title carries: no per-release URL has to be kept
        in step with the docs, and a model announced after this code was written
        still gets its introduction. A title names one generation, so the match
        stays one-way — a live model never absorbs a superseded generation's entry.
        """
        entry = entry_for_model(self.entries(), model_id)
        if entry is None or not entry.summary:
            return None
        return description_record(
            model_id,
            entry_model_label(entry),
            entry.summary,
            DEEPSEEK_UPDATES_URL,
            self.source_kind,
            source_name=self.source_name,
            lifecycle=entry_lifecycle(entry),
        )


class AliyunDescriptionSource(DescriptionSource):
    source_id = "aliyun"
    source_name = "阿里云百炼"
    source_url = ALIYUN_MODELS_URL
    source_kind = "anonymous_api"

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        metadata = (record or {}).get("model_metadata") or {}
        item: dict[str, Any] = {}
        if not metadata.get("summary"):
            key = normalize_model(model_id)
            item = next(
                (
                    value
                    for value in qianwen_catalogue(
                        self.client, query=model_id, page_size=20
                    )
                    if normalize_model(str(value.get("Model") or "")) == key
                ),
                {},
            )
            metadata = qianwen_model_metadata(item) if item else {}
        if not metadata.get("summary"):
            return None
        return description_record(
            model_id,
            item.get("Name") or display_name or model_id,
            metadata["summary"],
            metadata.get("source_url") or self.source_url,
            self.source_kind,
            source_name=self.source_name,
            capabilities=metadata.get("capabilities") or [],
            lifecycle=metadata.get("lifecycle") or ACTIVE,
            specifications=metadata.get("specifications") or {},
        )


class VolcengineDescriptionSource(DescriptionSource):
    """Use the public Model Square page when it server-renders the requested model."""

    source_id = "volcengine"
    source_name = "火山引擎方舟"
    source_url = VOLCENGINE_MODELS_URL
    source_kind = "official_console"

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        url = f"{self.source_url}/detail?name={quote(model_id, safe='')}"
        tags = meta_tags(self.client.get_text(url))
        summary = tags.get("description") or tags.get("og:description", "")
        title = tags.get("og:title") or ""
        if not summary or not model_mentioned(
            f"{title} {summary}", model_id, display_name
        ):
            return None
        return description_record(
            model_id,
            display_name or model_id,
            summary,
            url,
            self.source_kind,
            source_name=self.source_name,
            lifecycle=lifecycle_from_text(f"{title} {summary}"),
        )


# What an OpenRouter entry states about a model, in the words the report is
# written in. The API publishes modality and parameter names as identifiers, and
# these are the labels a reader is given for them; a value with no label is
# printed as the API spelled it rather than dropped.
OPENROUTER_MODALITY_LABELS = {
    "text": "文本",
    "image": "图像",
    "file": "文件",
    "audio": "音频",
    "video": "视频",
    "speech": "语音合成",
    "transcription": "语音转写",
    "embeddings": "向量",
    "rerank": "重排",
    "decisions": "决策",
}
OPENROUTER_PARAMETER_LABELS = (
    ("tools", "工具调用"),
    ("structured_outputs", "结构化输出"),
    ("reasoning", "推理"),
    ("response_format", "响应格式"),
)
# Said of an entry that publishes no rate at all, in place of the capabilities a
# priced model would list. What a reader must not take from the price block is a
# charge of nothing that was never stated: a free variant states one, a model
# still under test does not.
OPENROUTER_NO_CHARGE_LABELS = {
    "free": "官方公布价格为 0（免费档位或测试期），未作为价格记录",
    "varies": "价格随路由到的模型而定，官方未公布固定价格",
}


def openrouter_modal_text(modalities: Any, direction: str) -> str:
    """One direction's modalities as a person reads them, or ``""``."""
    values = [str(value) for value in modalities or []]
    if not values:
        return ""
    labels = "、".join(OPENROUTER_MODALITY_LABELS.get(value, value) for value in values)
    return f"{direction}：{labels}"


def openrouter_capabilities(
    entry: dict[str, Any], *, billed_elsewhere: bool = False
) -> list[str]:
    """What an entry states about a model, including that it charges nothing."""
    architecture = entry.get("architecture") or {}
    supported = set(entry.get("supported_parameters") or [])
    stated = [
        openrouter_modal_text(architecture.get("input_modalities"), "输入"),
        openrouter_modal_text(architecture.get("output_modalities"), "输出"),
    ]
    state = openrouter_pricing_state(entry, billed_elsewhere=billed_elsewhere)
    return [
        *(value for value in stated if value),
        *(
            label
            for parameter, label in OPENROUTER_PARAMETER_LABELS
            if parameter in supported
        ),
        *([OPENROUTER_NO_CHARGE_LABELS[state]] if state else []),
    ]


def openrouter_specifications(entry: dict[str, Any]) -> dict[str, str]:
    """The limits and identifiers an entry publishes beside its description.

    A limit the API leaves at zero states nothing — it is what a media model that
    has no token window publishes — so it is left out rather than reported as a
    window of no tokens.
    """
    top_provider = entry.get("top_provider") or {}
    specifications = {
        "context_window": entry.get("context_length"),
        "max_output_tokens": top_provider.get("max_completion_tokens"),
        "tokenizer": (entry.get("architecture") or {}).get("tokenizer"),
        "knowledge_cutoff": entry.get("knowledge_cutoff"),
    }
    stated = {
        key: value
        for key, value in specifications.items()
        if value not in (None, "", 0)
    }
    if entry.get("expiration_date"):
        stated["sunset_note"] = f"{entry['expiration_date']} 停止提供"
    return stated


class OpenRouterDescriptionSource(DescriptionSource):
    """OpenRouter's own introduction for the models it aggregates.

    The catalogue carries a paragraph for every entry it lists, which is what a
    reader needs to judge a price: what the model takes in and puts out, and — for
    an entry OpenRouter charges nothing for — that there is no price to judge. That
    last fact lives here rather than on the price record because it is a statement
    about the model's listing: a rate of zero and a model with no rate at all are
    the same number and two different things.
    """

    source_id = "openrouter"
    source_name = "OpenRouter"
    source_url = OPENROUTER_MODELS_URL
    source_kind = "anonymous_api"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._catalogue: dict[str, dict[str, Any]] | None = None
        self._videos: dict[str, dict[str, Any]] | None = None

    def _entries(self) -> dict[str, dict[str, Any]]:
        if self._catalogue is None:
            self._catalogue = {
                normalize_model(str(entry["id"])): entry
                for entry in openrouter_entries(self.client)
            }
        return self._catalogue

    def _billed_elsewhere(self, entry: dict[str, Any]) -> bool:
        """Whether the vendor prices this entry in its other document.

        Read only for an entry whose own rates are all zero, so a model that states
        a price never pays for a second document — and a model the video API prices
        per second is not described as one OpenRouter charges nothing for.
        """
        if openrouter_pricing_state(entry) is None:
            return False
        if self._videos is None:
            self._videos = openrouter_video_entries(self.client)
        return bool(
            (self._videos.get(openrouter_model_id(str(entry["id"]))) or {}).get(
                "pricing_skus"
            )
        )

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        entries = self._entries()
        entry = entries.get(normalize_model(model_id)) or entries.get(
            normalize_model(display_name)
        )
        if not entry or not clean_text(entry.get("description") or ""):
            return None
        return description_record(
            model_id,
            display_name or str(entry.get("name") or model_id),
            str(entry["description"]),
            f"{OPENROUTER_SITE_URL}/{entry['id']}",
            self.source_kind,
            source_name=self.source_name,
            capabilities=openrouter_capabilities(
                entry, billed_elsewhere=self._billed_elsewhere(entry)
            ),
            lifecycle=(LEGACY if entry.get("expiration_date") else ACTIVE),
            specifications=openrouter_specifications(entry),
        )


class TencentMirrorDescriptionSource(DescriptionSource):
    """Read the explicitly maintained mirror of TokenHub's authenticated list."""

    source_id = "tencent"
    source_name = "腾讯云 TokenHub"
    source_url = "https://console.cloud.tencent.com/tokenhub/models?regionId=1"
    source_kind = "authenticated_mirror"

    def __init__(self, client: Any, path: Path) -> None:
        super().__init__(client)
        self.path = path

    def _models(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceError("Tencent model-description mirror is unreadable") from exc
        try:
            validate_tencent_mirror(payload)
        except ValueError as exc:
            raise SourceError(
                f"Tencent model-description mirror is invalid: {exc}"
            ) from exc
        models: dict[str, dict[str, Any]] = {}
        for item in payload["models"]:
            for value in [item["model_id"], *(item.get("aliases") or [])]:
                models[normalize_model(value)] = item
        return models

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        models = self._models()
        item = models.get(normalize_model(model_id)) or models.get(
            normalize_model(display_name)
        )
        if not item:
            return None
        return description_record(
            model_id,
            item.get("display_name") or display_name or model_id,
            item.get("summary", ""),
            item.get("source_url") or self.source_url,
            self.source_kind,
            source_name=self.source_name,
            capabilities=item.get("capabilities") or [],
            lifecycle=item.get("lifecycle") or UNKNOWN,
            specifications=item.get("specifications") or {},
        )


DESCRIPTION_SOURCE_CLASSES = (
    OpenAIDescriptionSource,
    AnthropicDescriptionSource,
    GeminiDescriptionSource,
    XAIDescriptionSource,
    DeepSeekDescriptionSource,
    KimiDescriptionSource,
    ZhipuDescriptionSource,
    MiniMaxDescriptionSource,
    XiaomiDescriptionSource,
    AliyunDescriptionSource,
    VolcengineDescriptionSource,
    OpenRouterDescriptionSource,
    KlingDescriptionSource,
)
