"""Read vendor-published model retirement milestones without guessing availability.

Price catalogues and retirement notices answer different questions. These readers
keep the vendor's model id, platform scope, dates, and redirect behavior together;
an unreadable notice is never interpreted as a withdrawn model.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from .errors import SourceError
from .models import normalize_model
from .parsing import headed_document_tables, markdown_tables
from .text import clean_text

LifecycleEvent = dict[str, Any]

SOURCES = {
    "volcengine": "https://docs.volcengine.com/docs/ark/model-deprecation-notice",
    "tencent": "https://cloud.tencent.com/document/product/1823/130758",
    "baidu": "https://cloud.baidu.com/doc/qianfan/s/zmh4stou3",
    "deepseek": "https://api-docs.deepseek.com/zh-cn/updates",
    "kimi": "https://platform.kimi.com/docs/models.md",
    "xiaomi": "https://mimo.mi.com/static/docs/updates/deprecate.md",
    "openai": "https://developers.openai.com/api/docs/deprecations.md",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/model-deprecations.md",
    "google": "https://ai.google.dev/gemini-api/docs/deprecations",
    "xai": "https://docs.x.ai/llms.txt",
}

ENGLISH_DATE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)
NUMERIC_DATE = re.compile(
    r"(?P<year>20\d\d)\s*(?:年|[-/.])\s*(?P<month>\d{1,2})"
    r"\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
    r"(?:\s*(?P<clock>\d{1,2}:\d{2}(?::\d{2})?))?"
)
MODEL_ID = re.compile(r"[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+", re.I)


def date_value(value: str, *, utc_offset: str = "") -> str | None:
    """Keep the published precision; only attach an offset when the page states it."""
    value = clean_text(value).replace("\u200b", "")
    match = NUMERIC_DATE.search(value)
    if match:
        try:
            day = (
                datetime(int(match["year"]), int(match["month"]), int(match["day"]))
                .date()
                .isoformat()
            )
        except ValueError:
            return None
        clock = match["clock"]
        return f"{day}T{clock}{utc_offset}" if clock else day
    match = ENGLISH_DATE.search(value)
    if match:
        candidate = match.group().replace(",", "")
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def event(
    model_id: str,
    source_url: str,
    *,
    announced_at: str | None = None,
    eom_at: str | None = None,
    redirect_at: str | None = None,
    eos_at: str | None = None,
    replacement: str | None = None,
    end_behavior: str = "unknown",
    eos_earliest: bool = False,
    scope: str = "API",
) -> LifecycleEvent:
    return {
        "model_id": model_id,
        "announced_at": announced_at,
        "eom_at": eom_at,
        "redirect_at": redirect_at,
        "eos_at": eos_at,
        "replacement": replacement,
        "end_behavior": end_behavior,
        "eos_earliest": eos_earliest,
        "scope": scope,
        "source_url": source_url,
    }


def model_ids(value: str) -> list[str]:
    """Read literal ids only from the vendor's model column or model-id phrase."""
    return list(dict.fromkeys(MODEL_ID.findall(clean_text(value))))


def aliyun_events(records: list[dict[str, Any]]) -> list[LifecycleEvent]:
    """The public model-market API already embeds each model's OfflineTime."""
    found = []
    for record in records:
        metadata = record.get("model_metadata") or {}
        note = (metadata.get("specifications") or {}).get("sunset_note") or ""
        raw = str(note).removesuffix(" 下线").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            when = raw
        else:
            try:
                moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone(timedelta(hours=8)))
                when = moment.astimezone(timezone(timedelta(hours=8))).isoformat(
                    timespec="seconds"
                )
            except ValueError:
                when = date_value(raw, utc_offset="+08:00")
        if when:
            found.append(
                event(
                    record["model_id"],
                    record.get("source", {}).get("url")
                    or f"https://www.qianwenai.com/models/{record['model_id']}",
                    eos_at=when,
                    end_behavior="unavailable",
                    scope="百炼推理",
                )
            )
    return found


def volcengine_events(document: str) -> list[LifecycleEvent]:
    """Pair each official batch's timeline with its following model table."""
    tables = [rows for _, rows in headed_document_tables(document)]
    found = []
    for index, timeline in enumerate(tables[:-1]):
        if not timeline or "节点" not in clean_text(timeline[0][0]):
            continue
        models = tables[index + 1]
        if not models or "模型 ID" not in clean_text(models[0][0]):
            continue
        times: dict[str, str] = {}
        exception: dict[str, str] = {}
        for row in timeline[1:]:
            label = clean_text(row[0]).replace("\u200b", "")
            value = clean_text(row[1]).replace("\u200b", "") if len(row) > 1 else ""
            if "启动" in label:
                times["announced_at"] = date_value(value, utc_offset="+08:00") or ""
            elif "EOM" in label:
                times["eom_at"] = date_value(value, utc_offset="+08:00") or ""
            elif "EOS" in label:
                times["eos_at"] = date_value(value, utc_offset="+08:00") or ""
                for note in re.finditer(
                    r"(?P<ids>(?:[a-z][a-z0-9_.-]+\s*(?:及|、|,)?\s*)+)"
                    r"模型的\s*EOS\s*时间[：:]\s*(?P<date>[^。]+)",
                    value,
                    re.I,
                ):
                    override = date_value(note["date"], utc_offset="+08:00")
                    if override:
                        exception.update(
                            {name: override for name in model_ids(note["ids"])}
                        )
        if not times.get("eom_at") and not times.get("eos_at"):
            continue
        headings = [clean_text(value) for value in models[0]]
        suggested_column = next(
            (i for i, value in enumerate(headings) if "建议迁移" in value), None
        )
        system_column = next(
            (i for i, value in enumerate(headings) if "系统替换" in value), None
        )
        for row in models[1:]:
            ids = model_ids(row[0])
            if not ids:
                continue
            model_id = ids[0]
            is_embedding = "embedding" in normalize_model(model_id)
            system_text = (
                row[system_column]
                if system_column is not None and system_column < len(row)
                else ""
            )
            system_names = model_ids(system_text)
            suggested_text = (
                row[suggested_column]
                if suggested_column is not None and suggested_column < len(row)
                else ""
            )
            suggested_names = model_ids(suggested_text)
            replacement = (system_names or suggested_names or [None])[0]
            behavior = (
                "redirect"
                if system_names
                else "unavailable" if "关停" in system_text else "unknown"
            )
            found.append(
                event(
                    model_id,
                    SOURCES["volcengine"],
                    announced_at=times.get("announced_at") or None,
                    eom_at=times.get("eom_at") or None,
                    eos_at=(
                        None
                        if is_embedding
                        else exception.get(model_id, times.get("eos_at") or None)
                    ),
                    replacement=replacement,
                    end_behavior=(
                        "existing_access_continues" if is_embedding else behavior
                    ),
                    scope="方舟接入点",
                )
            )
    return found


def baidu_events(document: str) -> list[LifecycleEvent]:
    """Use the historical register, excluding its explicitly illustrative row."""
    found = []
    for headings, rows in headed_document_tables(document):
        if "完整模型退役历史记录" not in " ".join(headings) or not rows:
            continue
        headers = [clean_text(value) for value in rows[0]]
        if "登记日期" not in headers or "退役日期" not in " ".join(headers):
            continue
        model_column = (
            headers.index("退役模型版本")
            if "退役模型版本" in headers
            else headers.index("基础模型版本") if "基础模型版本" in headers else None
        )
        if model_column is None:
            continue
        announced_column = headers.index("登记日期")
        end_column = next(i for i, value in enumerate(headers) if "退役日期" in value)
        replacement_column = (
            headers.index("推荐替换模型") if "推荐替换模型" in headers else None
        )
        for row in rows[1:]:
            if max(model_column, end_column) >= len(row) or "示意" in " ".join(row):
                continue
            announced = (
                date_value(row[announced_column])
                if announced_column < len(row)
                else None
            )
            eos = date_value(row[end_column])
            if not eos:
                continue
            replacement = (
                clean_text(row[replacement_column])
                if replacement_column is not None and replacement_column < len(row)
                else None
            )
            for name in model_ids(row[model_column]):
                found.append(
                    event(
                        name,
                        SOURCES["baidu"],
                        announced_at=announced,
                        eos_at=eos,
                        replacement=replacement,
                        end_behavior="unavailable",
                        scope="千帆模型服务",
                    )
                )
    return found


def openai_events(document: str) -> list[LifecycleEvent]:
    found = []
    for path, rows in markdown_tables(document):
        if (
            not rows
            or "Shutdown date" not in rows[0][0]
            or len(rows[0]) < 2
            or "Model" not in rows[0][1]
        ):
            continue
        announced = date_value(path[-1]) if path else None
        for row in rows[1:]:
            if len(row) < 2 or not (eos := date_value(row[0])):
                continue
            # Backticks distinguish actual model ids from endpoint names and prose.
            # The official Markdown escapes pipes between aliases; the shared
            # table reader may still split them into extra cells.
            names = re.findall(r"`([^`]+)`", " ".join(row[1:-1]))
            replacement = re.findall(r"`([^`]+)`", row[-1]) if len(row) > 2 else []
            for name in names:
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
                    found.append(
                        event(
                            name,
                            SOURCES["openai"],
                            announced_at=announced,
                            eos_at=eos,
                            replacement=replacement[0] if replacement else None,
                            end_behavior="unavailable",
                            scope="OpenAI API",
                        )
                    )
    return found


def anthropic_events(document: str) -> list[LifecycleEvent]:
    """Only deprecated/retired rows are promises; active 'not sooner' is not."""
    for _, rows in markdown_tables(document):
        if rows and rows[0][:2] == ["API model name", "Current state"]:
            return [
                event(
                    row[0],
                    SOURCES["anthropic"],
                    announced_at=date_value(row[2]),
                    eos_at=date_value(row[3]),
                    end_behavior="unavailable",
                    scope="Anthropic-operated API",
                )
                for row in rows[1:]
                if len(row) >= 4
                and row[1] in ("Deprecated", "Retired")
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", row[0])
            ]
    raise SourceError("Anthropic model status table was not found")


def gemini_events(document: str) -> list[LifecycleEvent]:
    """Google calls each listed shutdown date the earliest possible date."""
    found = []
    for headings, rows in headed_document_tables(document):
        if not rows or rows[0][:3] != ["Model", "Release date", "Shutdown date"]:
            continue
        if "Managed agents" in headings:
            continue
        for row in rows[1:]:
            if len(row) < 3 or not (eos := date_value(row[2])) or not model_ids(row[0]):
                continue
            found.append(
                event(
                    row[0],
                    SOURCES["google"],
                    eos_at=eos,
                    eos_earliest=True,
                    replacement=row[3] if len(row) > 3 else None,
                    scope="Gemini API",
                )
            )
    return found


def kimi_events(document: str) -> list[LifecycleEvent]:
    section = document.split("## 已下线模型", 1)[-1]
    if section == document:
        raise SourceError("Kimi retired-model section was not found")
    dates: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith(">") or not (when := date_value(line)):
            continue
        names = [name for name in re.findall(r"`([^`]+)`", line) if model_ids(name)]
        if names:
            dates[names[0]] = when
    names: list[str] = []
    for _, rows in markdown_tables("## 已下线模型\n" + section):
        if not rows or "模型名称" not in rows[0][0]:
            continue
        for row in rows[1:]:
            names.extend(re.findall(r"`([^`]+)`", row[0]))
    names.extend(dates)
    found = []
    for name in dict.fromkeys(names):
        matches = [key for key in dates if name == key or name.startswith(key + "-")]
        if matches:
            found.append(
                event(
                    name,
                    SOURCES["kimi"],
                    eos_at=dates[max(matches, key=len)],
                    end_behavior="unavailable",
                    scope="Kimi API",
                )
            )
    return found


def xiaomi_events(document: str) -> list[LifecycleEvent]:
    tables = [rows for _, rows in headed_document_tables(document)]
    found = []
    for rows in tables:
        if not rows or "Deprecated Time" not in rows[0]:
            continue
        header = rows[0]
        model_col = 0
        eos_col = header.index("Deprecated Time")
        redirect_col = (
            header.index("System replacement time")
            if "System replacement time" in header
            else None
        )
        replacement_col = (
            header.index("System Replacement Model")
            if "System Replacement Model" in header
            else None
        )
        for row in rows[1:]:
            if eos_col >= len(row) or not (
                eos := date_value(row[eos_col], utc_offset="+08:00")
            ):
                continue
            redirect_at = (
                date_value(row[redirect_col], utc_offset="+08:00")
                if redirect_col is not None and redirect_col < len(row)
                else None
            )
            replacement = (
                row[replacement_col]
                if replacement_col is not None and replacement_col < len(row)
                else None
            )
            found.append(
                event(
                    row[model_col],
                    SOURCES["xiaomi"],
                    redirect_at=redirect_at,
                    eos_at=eos,
                    replacement=replacement,
                    end_behavior="unavailable",
                    scope="MiMo API",
                )
            )
    return found


class NoticeLinkParser(HTMLParser):
    """Collect official announcement links and their visible titles."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.words: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.words = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.words.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            self.links.append((self.href, clean_text(" ".join(self.words))))
            self.href = None


def tencent_events(client: Any, index: str) -> list[LifecycleEvent]:
    """The TokenHub index links individual notices with exact model IDs and dates."""
    parser = NoticeLinkParser()
    parser.feed(index)
    notices = [
        urljoin(SOURCES["tencent"], href)
        for href, title in parser.links
        if "TokenHub" in title and "模型下线" in title and "/announce/detail/" in href
    ]
    found = []
    # The index is newest first. A bounded read stays inside the shared per-host
    # request budget, while archived events retain older notices on later scans.
    for url in dict.fromkeys(notices[:18]):
        page = client.get_text(url)
        body = clean_text(page).replace("\u200b", "")
        match = re.search(r"model\s*参数值[：:]\s*([^）)]+)", body, re.I)
        end = re.search(
            r"(北京时间\s*20\d\d\s*年\s*\d+\s*月\s*\d+\s*日\s*\d{1,2}:\d{2})\s*起\s*正式下线",
            body,
        )
        if not match or not end:
            continue
        eos = date_value(end[1], utc_offset="+08:00")
        if not eos:
            continue
        metadata_at = body.rfind("addTime")
        announced = (
            date_value(body[metadata_at : metadata_at + 100])
            if metadata_at >= 0
            else None
        )
        redirected = "系统将自动为您切换" in body or "自动升级" in body
        replacement = None
        replacement_match = re.search(r"系统将自动为您切换至\s*([^。；]+)", body)
        if replacement_match:
            replacement = clean_text(replacement_match[1]).removesuffix("模型")
        for name in model_ids(match[1]):
            found.append(
                event(
                    name,
                    url,
                    announced_at=announced,
                    eos_at=eos,
                    replacement=replacement,
                    end_behavior="redirect" if redirected else "unknown",
                    scope="TokenHub",
                )
            )
    return found


def xai_events(client: Any, index: str) -> list[LifecycleEvent]:
    """Discover migration notices from xAI's official documentation index."""
    links = re.findall(
        r"\((https://docs\.x\.ai/developers/migration/[^)]+\.md)\)", index
    )
    found = []
    for url in dict.fromkeys(links):
        document = client.get_text(url)
        headline = document.splitlines()[:12]
        redirect = date_value(" ".join(headline))
        if not redirect:
            continue
        timed = re.search(
            r"Effective\s+(?P<date>"
            + ENGLISH_DATE.pattern
            + r")\s+at\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)\s*PT",
            document,
            re.I,
        )
        if timed:
            hour = int(timed["hour"]) % 12 + (
                12 if timed["ampm"].upper() == "PM" else 0
            )
            local = datetime.fromisoformat(redirect).replace(
                hour=hour,
                minute=int(timed["minute"]),
                tzinfo=ZoneInfo("America/Los_Angeles"),
            )
            redirect = local.isoformat(timespec="minutes")
        announced = None
        match = re.search(r"(?:notice|announc\w*)[^.\n]{0,100}", document, re.I)
        if match:
            announced = date_value(match[0])
        for _, rows in markdown_tables(document):
            if not rows or "Model being retired" not in rows[0][0]:
                continue
            for row in rows[1:]:
                if len(row) < 2:
                    continue
                names = re.findall(r"`([^`]+)`", row[0])
                replacements = re.findall(r"`([^`]+)`", row[1])
                for name in names:
                    found.append(
                        event(
                            name,
                            url,
                            announced_at=announced,
                            redirect_at=redirect,
                            replacement=replacements[0] if replacements else None,
                            end_behavior="redirect",
                            scope="xAI API",
                        )
                    )
    return found


def deepseek_events(document: str) -> list[LifecycleEvent]:
    """Record only explicit old-version withdrawals in the official changelog."""
    match = re.search(
        r"(时间[：:]\s*20\d\d[-/]\d+[-/]\d+).*?(旧版本模型[^。]+现已下线[^。]+。)",
        clean_text(document),
        re.S,
    )
    if not match:
        return []
    announced = date_value(match[1])
    replacement_match = re.search(
        r"模型名称更改为\s*(deepseek-[a-z0-9-.]+)", match[0], re.I
    )
    replacement = replacement_match[1] if replacement_match else None
    return [
        event(
            name,
            SOURCES["deepseek"],
            announced_at=announced,
            eos_at=announced,
            end_behavior="redirect",
            replacement=replacement,
            scope="DeepSeek API",
        )
        for name in re.findall(r"deepseek-[a-z0-9-.]+", match[2], re.I)
        if name.lower() != (replacement or "").lower()
    ]


PARSERS: dict[str, Callable[[str], list[LifecycleEvent]]] = {
    "volcengine": volcengine_events,
    "baidu": baidu_events,
    "deepseek": deepseek_events,
    "kimi": kimi_events,
    "xiaomi": xiaomi_events,
    "openai": openai_events,
    "anthropic": anthropic_events,
    "google": gemini_events,
}


def read_events(
    provider_id: str, client: Any, records: list[dict[str, Any]]
) -> tuple[str | None, list[LifecycleEvent]]:
    """Read one provider's official evidence fresh on each scan."""
    if provider_id == "aliyun":
        found = aliyun_events(records)
        if not found:
            raise SourceError("Aliyun model market published no readable offline times")
        return "https://www.qianwenai.com/models", found
    url = SOURCES.get(provider_id)
    if not url:
        return None, []
    document = client.get_text(url)
    if provider_id == "tencent":
        found = tencent_events(client, document)
    elif provider_id == "xai":
        found = xai_events(client, document)
    else:
        found = PARSERS[provider_id](document)
    if not found:
        raise SourceError(
            f"{provider_id} published no readable model retirement milestones"
        )
    return url, found
