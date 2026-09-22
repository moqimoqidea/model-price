"""Official model-description sources, independent from price adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from ..errors import SourceError
from ..models import model_matches, normalize_model
from ..parsing import headed_document_tables, markdown_tables
from ..providers.aliyun import (
    ALIYUN_MODELS_URL,
    qianwen_catalogue,
    qianwen_model_metadata,
)
from ..text import CELL_BREAK_RE, clean_text
from .core import (
    ACTIVE,
    PREVIEW,
    RETIRED,
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
VOLCENGINE_MODELS_URL = "https://console.volcengine.com/ark/region:cn-beijing/model"

DEEPSEEK_NEWS = (
    (
        "https://api-docs.deepseek.com/zh-cn/news/news260910",
        ("deepseek-flash", "deepseek-v4.1-flash", "deepseek-v4-1-flash"),
        ACTIVE,
    ),
    (
        "https://api-docs.deepseek.com/zh-cn/news/news260821",
        ("deepseek-v4-flash-vision-exp",),
        RETIRED,
    ),
    (
        "https://api-docs.deepseek.com/zh-cn/news/news260813",
        ("deepseek-v4-pro",),
        ACTIVE,
    ),
    (
        "https://api-docs.deepseek.com/zh-cn/news/news260424",
        ("deepseek-v4",),
        PREVIEW,
    ),
)


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
    """Read model/summary tables from one official Markdown overview."""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._entries: list[dict[str, Any]] | None = None

    def _catalogue(self) -> list[dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        text = self.client.get_text(self.source_url)
        entries: list[dict[str, Any]] = []
        for headings, table in markdown_tables(text):
            if len(table) < 2 or len(table[0]) < 2:
                continue
            headers = [markdown_text(cell).lower() for cell in table[0]]
            model_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if any(word in header for word in ("模型", "model"))
                ),
                None,
            )
            summary_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if any(
                        word in header
                        for word in ("描述", "介绍", "特点", "description")
                    )
                ),
                None,
            )
            if model_index is None or summary_index is None:
                continue
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
                        "lifecycle": ACTIVE if lifecycle == UNKNOWN else lifecycle,
                        "url": (
                            urljoin(self.source_url, link)
                            if link
                            else self.source_url
                        ),
                    }
                )
        if not entries:
            raise SourceError("official model-description table was not found")
        self._entries = entries
        return entries

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
            capabilities=[entry["category"]] if entry["category"] else [],
            lifecycle=entry["lifecycle"],
        )


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
                        scenarios.setdefault(normalize_model(row[1]), []).append(
                            clean_text(row[0])
                        )
                continue
            if (
                not headers
                or "模型 ID" not in headers[0]
                or "能力支持" not in headers
            ):
                continue
            ability_index = headers.index("能力支持")
            limit_index = next(
                (
                    index
                    for index, value in enumerate(headers)
                    if "长度限制" in value
                ),
                None,
            )
            category = headings[-1] if headings else ""
            for row in table[1:]:
                if len(row) <= ability_index:
                    continue
                model = clean_text(row[0])
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
                entries[normalize_model(model)] = {
                    "model": model,
                    "capabilities": capabilities,
                    "limits": limits,
                    "category": category,
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
            "、".join(scenarios)
            if scenarios
            else "、".join(entry["capabilities"])
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
            lifecycle=ACTIVE,
        )


class DeepSeekDescriptionSource(DescriptionSource):
    source_id = "deepseek"
    source_name = "DeepSeek"
    source_url = "https://api-docs.deepseek.com/zh-cn/"
    source_kind = "official_document"

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = normalize_model(model_id).rsplit("/", 1)[-1]
        # Prefer an exact historical page before following live aliases.
        candidates = sorted(
            DEEPSEEK_NEWS,
            key=lambda item: 0 if key in map(normalize_model, item[1]) else 1,
        )
        for url, aliases, lifecycle in candidates:
            exact = key in {normalize_model(alias) for alias in aliases}
            related = any(model_matches(model_id, alias) for alias in aliases)
            if not exact and not related:
                continue
            tags = meta_tags(self.client.get_text(url))
            summary = tags.get("description") or tags.get("og:description", "")
            title = (tags.get("og:title") or aliases[0]).split(" | ", 1)[0]
            if not summary:
                continue
            return description_record(
                model_id,
                title,
                summary,
                url,
                self.source_kind,
                source_name=self.source_name,
                lifecycle=lifecycle,
            )
        return None


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
        url = f"{self.source_url}/detail?name={model_id}"
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
)
