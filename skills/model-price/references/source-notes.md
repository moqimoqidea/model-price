# Official sources

Prefer a representation the page publishes for machines over scraping its rendered
markup: its own Markdown copy when the tables are real Markdown tables, otherwise
its public structured JSON. Parse rendered HTML only when the vendor publishes
neither.

- Aliyun Bailian: anonymous model-center JSON API in the script. The page also
  offers a "复制 MD 格式" copy, but it is a 585 KB MDX document whose tables are
  still embedded HTML `<table>` blocks, and the JSON API also carries time bands,
  discounts, batch prices and the inference provider. The JSON stays the source.
- Volcengine Ark: the page's `getDocDetail` JSON, reading `Result.MDContent` — the
  Markdown its "复制markdown" button produces. `Result.Content` is the same
  document as Slate JSON and is no longer parsed. Markdown table headings keep the
  whole path (`大语言模型 / 在线推理（常规）`), which names the offer.
- Tencent Cloud TokenHub: embedded Slate JSON from the official catalog and pricing
  documents; preserve self-deployed and “原厂直供” rows. There is no Markdown
  endpoint: the "MD" button converts this same Slate data in the browser with
  remark, so reading the Slate is reading the button's own source.
- DeepSeek: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`; the model table is keyed by a `模型` header. Model columns carry footnote markers such as `deepseek-flash(1)`, so markers are stripped before matching. The current model is `deepseek-flash`; retired names (`deepseek-v4-flash`, `deepseek-v4-flash-vision-exp`) resolve through `RETIRED_MODEL_ALIASES`.
- Kimi: `https://platform.kimi.com/docs/llms.txt` indexes the chat pricing document as `pricing/chat.md`; dated variants such as `chat-k3.md` have also been served, so the whole `chat*` family is matched. Rows are JSON arrays shaped `[model, unit, cache hit, cache miss, output, context]`. The sibling documents (`batch`, `tools`, `limits`) are not per-model token tables and must stay out of the catalogue.
- Zhipu BigModel: `https://docs.bigmodel.cn/cn/guide/start/pricing.md`. The anonymous
  config API that used to be read only publishes the five promoted flagship cards,
  while this page carries the whole catalogue, so the Markdown is both simpler and
  far broader. Only headers naming a per-million-token rate are read; the
  per-request (`单价`) and per-character speech tables are skipped.
- MiniMax: `https://platform.minimax.cn/docs/guides/pricing-paygo.md`, split into the language-model and speech sections. `platform.minimaxi.com` serves the identical document.
- Xiaomi MiMo: `https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go`. The table's first column is the product line (`MiMo-V2.5 系列`), not a generic `Model` header, so the model column falls back to the leftmost non-price column. The page publishes the same models twice — `模型国内定价` in CNY and `模型海外定价` in USD — and only the CNY tables are read. The ASR table (billed per audio hour) and the plugin pricing section are not token pricing and are skipped.
- OpenAI: `https://developers.openai.com/api/docs/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Anthropic: `https://platform.claude.com/docs/en/about-claude/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Google Gemini: `https://ai.google.dev/gemini-api/docs/pricing.md.txt`. The page
  publishes a Markdown copy, so its `pricing-table` HTML classes are no longer read.
  A model section is an `h2`, its service tiers are `h3`, and the section's API ids
  are published on an italic link line (`*[`gemini-3.8-flash`](url)*`), which is a
  better model id than the heading slug the HTML carried. Sections that price tools,
  agents, or general notes are excluded by name.
- xAI Grok: `https://docs.x.ai/developers/pricing.md`. The rendered HTML page splits the text-pricing header across two rows with merged cells, which the shared table reader cannot align; the official Markdown keeps a single header row. Long-context billing tiers live in a trailing parenthetical in the model cell (`grok-4.6 (≥ 200k prompt tokens)`), so they are preserved as a `context_tier` condition rather than dropped with the model id.

Provider caches live under `cache/<provider>/`. A cache entry records its provider, operation, arguments, fetch time, schema version, and data. Entries older than 3 hours are not used as fallback when refresh fails, and `CACHE_SCHEMA_VERSION` is bumped whenever a source or parser changes so entries written by an older version are ignored.

## Parser maintenance

Identify rows by content — parsed prices, table headers, or section anchors — never by a hard-coded model-name prefix, family list, or document-name suffix. A vendor rename, a newly launched family, or a renamed pricing document must be picked up without a code change. Where filtering is unavoidable, prefer an explicit blocklist of non-model sections (see `NON_MODEL_SECTIONS` and `NON_MODEL_SECTION_IDS`) over an allowlist of model prefixes, and keep alias maps additive so they never drop an existing match.

Two conventions apply to every table-driven adapter:

- Keep cache-hit and cache-miss prices apart. A cache miss is billed at the regular input rate, so `输入（未命中缓存）` must not be classified as cached input. Cache *storage* is the exception that still counts as a token price even though it bills an hour.
- Read only amounts that name the adapter's own currency (`CNY` tables emit CNY, `USD` tables emit USD) and ignore headers that bill a non-token unit such as audio duration (`输入音频时长`, per hour) or per-request pricing. Never relabel one currency as another.

Two conventions come with the Markdown reader:

- Vendor Markdown is escaped (`deepseek\-v4\-flash正式版`, `输入长度 \[0, 32K)`). The reader removes those escapes before a cell is named, matched, or compared, and a row keeps its empty leading cells so a table with a carried model name stays column-aligned.
- A table is read with the whole heading path that precedes it, so a `h2` model (`Gemini 3.8 Flash`) and the `h3` tier under it (`Standard`) stay distinguishable. Inside a cell, `<br>` separates the model name from a note such as `调整前价格，2026-08-21 起不适用`; the note is preserved as a `model_note` condition instead of being dropped, because the price on that row no longer applies.
