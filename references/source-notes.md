# Official sources

Prefer a representation the page publishes for machines over scraping its rendered
markup: its own Markdown copy when the tables are real Markdown tables, otherwise
its public structured JSON. Parse rendered HTML only when the vendor publishes
neither.

## Model introductions

Introductions live in `model_price/descriptions/`, independently of price adapters.
The resolver first tries the model vendor and then the platform whose price record
named it. It returns one introduction per canonical model, so provider spellings
such as `deepseek-v4.1-flash`, `deepseek-v4-1-flash`, and `deepseek-flash` do not
produce duplicate prose. Query failures are isolated from prices; `delta` resolves
only models present in an actual change.

- OpenAI, Anthropic, Gemini, and xAI use the official per-model Markdown variants.
  A soft-404 page is accepted only when its body names the requested model.
- Kimi, MiniMax, and Zhipu use the official overview Markdown tables. Section
  headings contribute category/lifecycle information, including Kimi's explicit
  已下线 section.
- DeepSeek uses the official release/news pages. Their server-rendered metadata is
  the narrowest public representation carrying the release summary; exact retired
  ids are preferred before following a live alias.
- Aliyun uses the same anonymous model-centre API as pricing. Its `description`,
  `capabilities`, `features`, context limit, output limit, lifecycle, and `docUrl`
  fields are normalized by the description package; the gateway response is decoded
  by one shared helper rather than duplicated in two adapters.
- Xiaomi's public HTML is parsed only because it publishes no Markdown variant.
  Model capability and quick-selection tables are combined.
- Volcengine's public Model Square page is used only when its server-rendered
  metadata actually names the requested model; a generic shell description is
  rejected as a soft miss.
- Tencent model details require an authenticated console. The checked-in
  `descriptions/data/tencent-models.json` file is the explicit mirror. Absence from
  the mirror is `not_found`, never a cue to synthesize an introduction.

- Aliyun Bailian: anonymous model-center JSON API in the script. The page also
  offers a "复制 MD 格式" copy, but it is a 585 KB MDX document whose tables are
  still embedded HTML `<table>` blocks, and the JSON API also carries time bands,
  discounts, batch prices and the inference provider. The JSON stays the source.
- Volcengine Ark: the page's `getDocDetail` JSON, reading `Result.MDContent` — the
  Markdown its "复制markdown" button produces. `Result.Content` is the same
  document as Slate JSON and is no longer parsed. Markdown table headings keep the
  whole path (`大语言模型 / 在线推理（常规）`), which names the offer. Its condition
  column is headed `条件 输入长度：千 token`, so it mentions 输入 without being a
  price: it carries the length tier (`输入长度 [0, 32]`) and, for
  `deepseek-v4-1-flash`, the peak/off-peak band (`空闲时段` / `高峰时段`). Reading it
  as an input price column loses both, so it must be classified as a condition.
- Tencent Cloud TokenHub: embedded Slate JSON from the official catalog and pricing
  documents; preserve self-deployed and “原厂直供” rows. There is no Markdown
  endpoint: the "MD" button converts this same Slate data in the browser with
  remark, so reading the Slate is reading the button's own source.
- Tencent model introductions: the model square is authenticated and has no durable
  anonymous description endpoint, so its 100 rendered cards were captured on
  2026-09-17 into `descriptions/data/tencent-models.json`. Delivery-specific
  duplicates are collapsed into 98 model-level introductions, while API ids from
  the public catalogue are retained as aliases. `update_tencent_model_mirror.py`
  validates captures and rejects empty, duplicate, or conflicting entries before
  replacement.
- Baidu Qianfan: the pricing page is a Gatsby document. Its own HTML is the whole
  portal, and the Markdown behind its "查看 MD" button is assembled in the browser
  (it opens as a `blob:` URL) rather than published as a file — so the article body
  is read from the `page-data.json` the page itself pre-fetches, which is both an
  official structured source and far narrower than the rendered page. Its address
  is read out of that preload link instead of being hard-coded, because the CDN path
  carries the build's asset prefix. A row is priced along three axes at once: the
  billing item is named inside the row (`子项`: 输入 / 命中缓存 / 输出), the serving
  channel is a column (`在线推理` / `批量推理`), and the peak/off-peak window is
  written into the item's own text (`输入（高峰时段：8:00-22:00）`). Only
  `批量推理 （原价）` is read — the `2月活动价` / `3月活动价` columns are promotions
  that expire. Prices are quoted per thousand tokens and restated per million. The
  same page also prices token packages, TPM reservations, OCR pages, images, video,
  compute units and fine-tuning, none of which are token tables. Qianfan serves
  ERNIE alongside third-party families (DeepSeek, GLM, Qwen, Kimi); it does **not**
  serve DeepSeek-V4.1-Flash, only the superseded `DeepSeek-V4-Flash-0731`, which is
  filed as a model of its own and must never be merged into a `deepseek-flash`
  comparison.
- DeepSeek: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`; the model table is keyed by a `模型` header. Model columns carry footnote markers such as `deepseek-flash(1)`, so markers are stripped before matching. The current model is `deepseek-flash`; retired names (`deepseek-v4-flash`, `deepseek-v4-flash-vision-exp`) resolve through `RETIRED_MODEL_ALIASES`. Aliyun files the same live generation as `deepseek-v4.1-flash` and Ark as `deepseek-v4-1-flash`, so those go in `CURRENT_MODEL_ALIASES`, which is matched in both directions — a live-model label must never pull a superseded generation's price rows into the comparison, and `--exact` ignores it.
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

The baselines `delta` compares against live beside the cache under
`snapshots/<provider>.json` — one file per provider, replaced in place, and never
expiring, because an expiring baseline would turn every run into a first run. A
baseline records the catalogue only: models keyed by normalized id, each with its
offers, and each offer identified by its name plus the conditions saying what is
billed. `source_section` deliberately stays out of that identity, since it names
where the document filed the row and a renamed section would otherwise read as one
offer vanishing and another appearing; the condition itself is still kept for the
reader. `SNAPSHOT_SCHEMA_VERSION` is bumped when the shape changes, and a baseline
written by an older shape is treated as absent rather than diffed against.

Two catalogue-level traps:

- A scan must read the sources afresh. Serving a scan from the 3-hour cache would
  compare the previous scan's own data with itself and report "no change" for a
  vendor that did move, so `delta` always refreshes.
- Some vendors date their catalogue and most do not. Aliyun publishes a per-model
  `updateAt` and Ark a document-level `UpdatedTime`; both are kept as
  `source_updated_at`, and a scan that finds no price movement repeats the newest
  one as evidence. An absent stamp means the vendor publishes none — never that its
  prices are stale.

## Time bands

A vendor that bills by time of day states the window somewhere other than a price column, and every platform words it differently — so the hours are read per vendor and never carried across. Most state it in prose beside the table: `time_band_rules()` collects those sentences, `select_time_band_rules()` keeps the ones governing a model (matched by model name first, then by a delivery label such as `原厂直供`), and `compact_time_band_window()` reduces a sentence to the window repeated on each peak/off-peak row. Baidu instead writes the window into the billed item's own text, so `baidu.py` reads it off the rows. Either way the vendor's own wording stays on the record as `time_bands.statements`, because the wording is what settles the bill.

For `deepseek-flash` the platforms currently disagree:

| Provider | Peak / 忙时 | Off-peak / 闲时 |
|---|---|---|
| DeepSeek | 周一至周五 9:00–12:00、14:00–18:00 | 其余（含整个周末） |
| Volcengine Ark | 周一至周五 09:00–12:00、14:00–18:00 | 其余（含整个周末） |
| Tencent (原厂直供) | 工作日 9:00–12:00、14:00–18:00 | 其余（含周末全天） |
| Tencent (0731/0813 self-hosted) | 周一至周日 9:00–12:00、14:00–18:00 | 其余 |
| Aliyun Bailian | 08:00–22:00（其余时段为忙时） | 22:00–次日 08:00 |
| Baidu Qianfan (`DeepSeek-V4-Flash-0731`, not `deepseek-flash`) | 08:00–22:00 | 22:00–次日 08:00 |

The first three therefore agree, and Aliyun is the outlier: its cheap window is overnight only, so midday (12:00–14:00), evening (18:00–22:00) and the whole weekend cost double there but are off-peak everywhere else. Baidu happens to draw the same window as Aliyun, on a different generation of the model — the two are independent statements that happen to coincide, never a rule to apply to one another.

Three traps: a `；` joins clauses of one rule (Tencent states the weekday window and the weekend exemption in a single sentence), so sentences are split on `。` only; a rule quoted from a footnote without naming any model applies to everything the document lists; and a window stated in prose does not move a price — the band a row belongs to still comes from the cell, as described above.

## Parser maintenance

Identify rows by content — parsed prices, table headers, or section anchors — never by a hard-coded model-name prefix, family list, or document-name suffix. A vendor rename, a newly launched family, or a renamed pricing document must be picked up without a code change. Where filtering is unavoidable, prefer an explicit blocklist of non-model sections (see `NON_MODEL_SECTIONS` and `NON_MODEL_SECTION_IDS`) over an allowlist of model prefixes, and keep alias maps additive so they never drop an existing match.

A whole-catalogue scan reads each vendor once, so an adapter that already parses its
entire document exposes it through `catalog_records()` rather than being walked model
by model; `PriceSource` still provides a walking fallback so an adapter that only
knows how to answer a single model scans correctly without changes. Aliyun is the
one adapter whose `query` is itself an HTTP request, so it overrides `catalog_records`
to page its catalogue instead — asking that endpoint for a page of models with
`queryPrice` returns each model with its prices, turning 511 requests into 11.
Bailian's gateway throttles a burst of those pages, so every page after the first
waits a random pause of 1–3 seconds (`CATALOG_PAGE_PAUSE_SECONDS`); the jitter keeps
the walk off a fixed rhythm a throttle could lock onto. That pause is why a full
scan takes tens of seconds, and it is deliberate rather than a stall.
`CachedPriceSource` caches a scan as a single `catalog` entry, not one per model.

Two conventions apply to every table-driven adapter:

- Keep cache-hit and cache-miss prices apart. A cache miss is billed at the regular input rate, so `输入（未命中缓存）` must not be classified as cached input. Cache *storage* is the exception that still counts as a token price even though it bills an hour.
- Read only amounts that name the adapter's own currency (`CNY` tables emit CNY, `USD` tables emit USD) and ignore headers that bill a non-token unit such as audio duration (`输入音频时长`, per hour) or per-request pricing. Never relabel one currency as another.
- A header that describes the *request* instead of a price is a condition, not a price column. Length bands are the trap: `条件 输入长度：千 token` contains 输入, so an input-price rule would swallow the column and silently drop the tier that separates otherwise identical rows (see `NON_PRICE_HEADER_MARKERS`, and any adapter that classifies headers itself must consult it). The cell value, not the heading, decides a time-band key, because vendors file the peak/off-peak split in the same generic 条件 column.

Conventions that come with the document readers:

- Vendor Markdown is escaped (`deepseek\-v4\-flash正式版`, `输入长度 \[0, 32K)`). The reader removes those escapes before a cell is named, matched, or compared, and a row keeps its empty leading cells so a table with a carried model name stays column-aligned.
- A table is read with the whole heading path that precedes it, so a `h2` model (`Gemini 3.8 Flash`) and the `h3` tier under it (`Standard`) stay distinguishable.
- A cell break (`<br>`) survives both readers as written, so the values a vendor stacks in one cell stay separable: the first is the model, the rest are variants the same price covers (`ERNIE-5.0<br>ERNIE-5.0-Thinking-Preview`). A note stacked under a model name (`调整前价格，2026-08-21 起不适用`) is preserved as a `model_note` condition instead of being dropped, because the price on that row no longer applies.
- Cells are laid onto a real grid, repeating a value across every row a *row span* covers — a vendor writes such a cell once, and without the repeat the columns below it shift left and a price lands under the wrong heading. A *column span* is deliberately not repeated: it is how a vendor lays a row heading across the columns beside it, and expanding it would fill the header row — the row that says which columns are prices — with copies of the heading. A cell arriving with no open row starts one, because Baidu's own table drops one `<tr>` and those cells belong to that row, not to the one above.
- A vendor that quotes a rate per thousand or per ten thousand tokens is restated per million (`tokens_per_price_unit` / `per_million_tokens`), so one report compares one unit. The figure the vendor published stays in the price's `display` text. A label that does not price tokens at all (`元/页`, `元/次`) is rejected rather than rescaled.
