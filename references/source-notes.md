# Official sources

Prefer a representation the page publishes for machines over scraping its rendered
markup: its own Markdown copy when the tables are real Markdown tables, otherwise
its public structured JSON. Parse rendered HTML only when the vendor publishes
neither.

## Model retirement notices

Retirement dates are read independently of price tables on every `delta` run.
Do not infer an official EOS from a model disappearing from a price catalogue:
catalogue removal can also reflect a parser or billing change. The notice reader
keeps each hosting platform's literal model ID, published date precision,
replacement, behavior of the old ID, and exact source URL. An unreadable or empty
source keeps its last successful notice archive.

| Platform | Official evidence | Meaning and limits |
| --- | --- | --- |
| Volcengine Ark | [Model deprecation notice](https://docs.volcengine.com/docs/ark/model-deprecation-notice) | Read the page's official `getDocDetail` `Result.MDContent`, because the rendered HTML sometimes omits its tables. Batch timeline gives announcement/start, EOM, EOS; model rows may override EOS. Some embedding rows state EOM while existing access continues. |
| Tencent TokenHub | [Product announcement index](https://cloud.tencent.com/document/product/1823/130758) and its linked [individual notices](https://cloud.tencent.com/announce/detail/2469) | A notice gives exact `model` parameters, Beijing shutdown time, and possible automatic replacement. The general announcement feed also contains price notices, so title filtering alone is insufficient evidence of retirement. The index is a rolling list; previous notice records remain archived when a link rolls off. |
| Baidu Qianfan | [Retirement mechanism and history](https://cloud.baidu.com/doc/qianfan/s/zmh4stou3) | Historical table gives registration and retirement dates per hosted model, plus recommended replacement. Its example row is excluded. |
| Aliyun Bailian | [Deprecation policy](https://help.aliyun.com/zh/model-studio/model-depreciation) and public [model market](https://www.qianwenai.com/models) | The anonymous market API exposes per-model `OfflineInfo.Inference.OfflineTime`. Read it from the fresh catalogue; convert an explicit UTC instant to Beijing time, and keep an undated or missing value unknown. |
| DeepSeek | [Official updates](https://api-docs.deepseek.com/zh-cn/updates/) | The trailing slash is required: without it, the site can return a generic docs page with HTTP 200. Record only explicit old-version withdrawal and continued routing stated in the changelog; there is no complete future retirement timetable. |
| Kimi | [Model list](https://platform.kimi.com/docs/models) | Retired-model section gives series-level dates and literal retired IDs. Match a table ID to the longest published series prefix. |
| Xiaomi MiMo | [Deprecation log](https://mimo.mi.com/static/docs/updates/deprecate.md) | Separates the earlier automatic replacement time from the final old-ID expiry time where both are printed. |
| OpenAI | [API deprecations](https://developers.openai.com/api/docs/deprecations) | Published notification and shutdown tables. A row may name several aliases separated by escaped Markdown pipes; each literal ID gets its own event. |
| Anthropic | [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations) | Deprecated and retired table applies to Anthropic-operated API. A row explicitly marked Retired is down even if its retirement date is blank. Partner platform schedules may differ. Active models' “not sooner than” dates are not shutdown promises. |
| Google Gemini | [Gemini API deprecations](https://ai.google.dev/gemini-api/docs/deprecations) | Shutdown dates are the *earliest possible* dates. The page separately marks already-shutdown models with `row-gray` table rows, including one with no published shutdown date. Only that explicit row state confirms retirement; reaching an unshaded row's date does not. |
| xAI | [Official migration guides](https://docs.x.ai/developers/migration/may-15-retirement) | Old slugs can continue to resolve through automatic redirection, with billing at replacement-model prices. Read the effective PT clock where given; a date-only notice remains date-only. |
| Zhipu BigModel | [GLM-4.5-Flash](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.5-flash.md), [GLM-Z1](https://docs.bigmodel.cn/cn/guide/models/text/glm-z1.md), and [GLM-4.5](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5.md) model pages | No central retirement feed. Read explicit banner text on these known official pages: a dated shutdown with automatic routing, an undated “已下线” series notice, and undated “即将下线” plans. Only literal named IDs are recorded; a series notice is not expanded into guessed variants. These pages are a maintained source list, not a complete vendor-wide schedule. |
| MiniMax | [Official model overview](https://platform.minimax.io/docs/guides/models-intro.md) | `Legacy Models` accordions identify old versions but provide no shutdown date and do not prove service cessation. A separate music notice explicitly discontinues three free music API IDs on 2026-08-20; the paid API restriction applies only to new users and does not retire those models. |

Run `python3 scripts/audit_model_retirements.py` to read all 13 vendor-specific
sources fresh and print exact model IDs, milestones, status-only claims, source
URLs, and whether the evidence confirms retirement as of the chosen calendar
timezone. `--provider` can select one vendor; `--timezone` defaults to
`Asia/Shanghai`. The audit is read-only and does not write a scan baseline. The
offline tests in `tests/test_lifecycle.py` cover each vendor's source shape and
the distinction between a scheduled, legacy, earliest-possible, and confirmed
shutdown.

An announcement date, EOM (new purchases stop), automatic redirection, and EOS
(service shutdown) are distinct milestones. A date revision or a date crossing
is a change even if the price catalogue is identical. Date-only values are
compared by the scan's local calendar day; timed values are compared as instants.
The notice history is written under `snapshots/lifecycle-<provider>/`, separately
from price snapshots, and is never populated from the three-hour cache.

## Model introductions

Introductions live in `model_price/descriptions/`, independently of price adapters.
The resolver first tries the model vendor and then the platform whose price record
named it. It returns one introduction per canonical model, so provider spellings
such as `deepseek-v4.1-flash`, `deepseek-v4-1-flash`, and `deepseek-flash` do not
produce duplicate prose. Query failures are isolated from prices; `delta` resolves
only models present in an actual change.

- OpenAI, Gemini, and xAI use the official per-model Markdown variants. Anthropic
  first reads its official models-overview Markdown, which publishes each model's
  detail link, Claude API id, and alias, then follows that indexed detail page;
  punctuation in a price-table id is never used to guess a URL. A generated
  soft-404 page is accepted only when its body names the requested model. An
  Anthropic page named by the official index but missing or mismatched is a source
  error, while a model absent from the index is `not_found`. Per-model Markdown
  specifications come only from labelled bullets or vertical property/value
  tables; headings in horizontal comparison tables are never treated as values.
- Kimi, MiniMax, and Zhipu use the official overview Markdown tables. Section
  headings contribute category/lifecycle information, including Kimi's explicit
  已下线 section.
- DeepSeek uses the official release/news pages. Their server-rendered metadata is
  the narrowest public representation carrying the release summary. A page is
  attached only to its literal published ID or a dated build of that ID; a broad
  family match would misattribute the Vision-Exp release to a separate preview
  model in a hosting provider's price table.
- Aliyun uses the same public Qianwen model-market catalogue as pricing. Its
  `Description`, `Capabilities`, `Features`, context limits, modalities, and
  scheduled withdrawal are normalized by one shared helper. The introduction's
  source is the model's public `qianwenai.com/models/<id>` page; older documentation
  URLs carried in catalogue metadata are not used.
- Xiaomi's public HTML is parsed only because it publishes no Markdown variant.
  Model capability and quick-selection tables are combined.
- Volcengine's public Model Square page is used only when its server-rendered
  metadata actually names the requested model; a generic shell description is
  rejected as a soft miss. Model IDs are URL-encoded in its detail query, including
  Chinese release-stage suffixes.
- Tencent model details require an authenticated console. The checked-in
  `descriptions/data/tencent-models.json` file is the explicit mirror. Absence from
  the mirror is `not_found`, never a cue to synthesize an introduction.

- Aliyun Bailian: `https://www.qianwenai.com/models` and its public
  `ListModelSeries` POST endpoint at `platform-home.qianwenai.com`. The endpoint
  accepts `PageNo`, `PageSize`, `Language`, and an optional `Query`; it returns the
  series in `Data[]` and independently priced models in each series' `Items[]`.
  Requests made without cookies, `sec_token`, or any authorization header return
  the full catalogue. A page size of 200 currently reads every series at once, and
  the adapter retains pagination so later catalogue growth is not cut off.
  `Prices[]` carries direct prices and `MultiPrices[]` carries input-length or other
  tiers, each of which remains a separate offer. `Discount` is a multiplier: the
  effective amount is `Price × Discount`, while `Price` and `Discount` remain as
  `list_amount` and `discount` for auditability. `BuiltInToolMultiPrices` describes
  optional tool calls rather than model inference, so those charges are not folded
  into a model's token or generation offers. Model detail URLs percent-encode the
  literal model id; their server-rendered tooltip is the official source for the
  peak/off-peak window. Catalogue fields also supply the model introduction,
  capabilities, modalities, limits, update time, and scheduled withdrawal.
- Volcengine Ark: the page's `getDocDetail` JSON, reading `Result.MDContent` — the
  Markdown its "复制markdown" button produces. `Result.Content` is the same
  document as Slate JSON and is no longer parsed. Markdown table headings keep the
  whole path (`大语言模型 / 在线推理（常规）`), which names the offer. Its condition
  column is headed `条件 输入长度：千 token`, so it mentions 输入 without being a
  price: it carries the length tier (`输入长度 [0, 32]`) and, for
  `deepseek-v4-1-flash`, the peak/off-peak band (`空闲时段` / `高峰时段`). Reading it
  as an input price column loses both, so it must be classified as a condition.
  The same document prices image generation per image (`元/张`); those rows are
  excluded from the token catalogue. Older local baselines that mislabeled such
  rows as token prices are filtered on read without rewriting the archive, so
  correcting the parser does not report image products as newly removed models.
- Tencent Cloud TokenHub: embedded Slate JSON from the official catalog and pricing
  documents; preserve self-deployed and “原厂直供” rows. There is no Markdown
  endpoint: the "MD" button converts this same Slate data in the browser with
  remark, so reading the Slate is reading the button's own source. The same article
  payload carries `recentReleaseTime`, which is the price page's official update
  time and is stored with every parsed record.
- Tencent model introductions: the model square is authenticated and has no durable
  anonymous description endpoint. The Guangzhou model square was captured through
  the user's signed-in Chrome session on 2026-09-24. Its first page rendered 100
  cards; author filters exposed all 114 cards, which collapse into 112 model-level
  introductions in `descriptions/data/tencent-models.json`. API ids from
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
  comparison. The rendered page's labelled 更新时间 is retained as the catalogue's
  official update date; it is metadata beside the structured article, not a price
  parsed from the shell.
- DeepSeek: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`; the
  model table is keyed by a `模型` header. Model columns carry footnote markers
  such as `deepseek-flash(1)`, so markers are stripped before matching. The current
  model is `deepseek-flash`; retired names (`deepseek-v4-flash`,
  `deepseek-v4-flash-vision-exp`) resolve through `RETIRED_MODEL_ALIASES`. Aliyun
  files the same live generation as `deepseek-v4.1-flash` and Ark as
  `deepseek-v4-1-flash`, so those go in `CURRENT_MODEL_ALIASES`, which is matched
  in both directions — a live-model label must never pull a superseded generation's
  price rows into the comparison, and `--exact` ignores it. The pricing page has no
  update stamp, so a scan checks the latest seven date-shaped official news URLs.
  Missing Docusaurus routes render the docs home page with HTTP 200; a page counts
  only when its canonical URL or document id matches the candidate date. With no
  recent news hit, the message uses the previous successful snapshot time and
  labels it 上次更新时间.
- Kimi: `https://platform.kimi.com/docs/llms.txt` indexes the chat pricing document as `pricing/chat.md`; dated variants such as `chat-k3.md` have also been served, so the whole `chat*` family is matched. The Markdown embeds JSX `DocTable` blocks whose columns are declarations and whose rows are JSON arrays. Prices are mapped by those column titles because K3 inserted two cache-write TTL columns ahead of cache-hit input; assigning by the old positions would turn `20 / 40 / 2` into a false cache/input/output move. Headerless legacy captures still use the former `[model, unit, cache hit, cache miss, output, context]` shape. The sibling documents (`batch`, `tools`, `limits`) are not per-model token tables and must stay out of the catalogue.
- Zhipu BigModel: `https://docs.bigmodel.cn/cn/guide/start/pricing.md`. The anonymous
  config API that used to be read only publishes the five promoted flagship cards,
  while this page carries the whole catalogue, so the Markdown is both simpler and
  far broader. Only headers naming a per-million-token rate are read; the
  per-request (`单价`) and per-character speech tables are skipped.
- MiniMax: `https://platform.minimax.cn/docs/guides/pricing-paygo.md`, split into the language-model and speech sections. `platform.minimaxi.com` serves the identical document.
- Xiaomi MiMo: `https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go`. The
  older table's first column was the product line (`MiMo-V2.5 系列`), so that
  labelled series column remains a fallback. Current tables explicitly label
  `模型名称` after `推理类型`; one cell can name several model IDs separated by `、`,
  and each gets its own priced record. `实时推理` and `批量推理` remain separate
  offers, with batch excluded from the default standard-price message. A
  parenthetical `即将下线` is not a context tier or price condition; the dated
  retirement evidence comes from the independent deprecation log. The page
  publishes the same models twice — `模型国内定价` in CNY and `模型海外定价` in USD —
  and only the CNY tables are read. The ASR table (billed per audio hour) and
  plugin pricing are not token pricing. The labelled 更新时间 is the official
  catalogue update date.
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

All live reads pass through the standard-library `HttpClient`. GET and HEAD use at
most three attempts for connection failures and HTTP 408, 425, 429, 500, 502, 503,
and 504, with full-jitter exponential backoff capped at 8 seconds. A Cloudflare
challenge-shaped 403 is retried once; other 4xx responses fail immediately.
`Retry-After` is honored for 429 and 503 when its delay is at most 30 seconds, and
longer waits end the provider early with the requested delay in the error. POST is
one attempt unless the caller explicitly marks its read-only operation idempotent;
the Bailian catalogue POST is such a read. Each host has a 20-attempt budget per
run, including retries. The same GET URL and the same cached operation are held in
memory for the run, while every registered provider builds a whole catalogue from
one parsed source pass. Errors distinguish timeout, connection failure, rejected
4xx, and server 5xx responses and always retain a non-empty diagnostic.

The baselines `delta` compares against live beside the cache as timestamped files
under `snapshots/<provider>/`. Every successful scan is archived, even when its
catalogue is unchanged. Retention is hard-capped at 1000 snapshots per provider:
the last scan of each day in the recent three-month calendar window is reserved,
then the remaining slots take the newest scans. The legacy
`snapshots/<provider>.json` shape remains readable and is migrated on the next
successful scan; an unreadable legacy file moves to `snapshots/rejected/`. A
baseline records the catalogue only: models keyed by normalized
id, each with its offers, and each offer identified by its name plus the conditions
saying what is billed. `source_section` deliberately stays out of that identity,
since it names where the document filed the row and a renamed section would
otherwise read as one offer vanishing and another appearing; the condition itself
is still kept for the reader. `SNAPSHOT_SCHEMA_VERSION` is bumped when the shape
changes, and a baseline written by an older shape is treated as absent rather than
diffed against.

Two catalogue-level traps:

- A scan must read the sources afresh. Serving a scan from the 3-hour cache would
  compare the previous scan's own data with itself and report "no change" for a
  vendor that did move, so `delta` always refreshes.
- Vendor update evidence stays separate from scan time. Aliyun publishes per-model
  `UpdateAt`, Ark publishes document-level `UpdatedTime`, Tencent publishes
  `recentReleaseTime`, and Baidu and Xiaomi label a page date; these are kept as
  `source_updated_at`, and a scan repeats the newest one as official evidence.
  DeepSeek contributes a date only when an official news page exists in the latest
  seven-day window. For Kimi, Zhipu, MiniMax, and any other source with no official
  stamp, the message uses the previous successful snapshot's `captured_at` and
  labels it 上次更新时间. An absent official stamp never means the prices are stale.

## Time bands

A vendor that bills by time of day states the window somewhere other than a price column, and every platform words it differently — so the hours are read per vendor and never carried across. Most state it in prose beside the table: `time_band_rules()` collects those sentences, `select_time_band_rules()` keeps the ones governing a model (matched by model name first, then by a delivery label such as `原厂直供`), and `compact_time_band_window()` reduces a sentence to the window repeated on each peak/off-peak row. Baidu instead writes the window into the billed item's own text, so `baidu.py` reads it off the rows. Either way the vendor's own wording stays on the record as `time_bands.statements`, because the wording is what settles the bill.

For `deepseek-flash` the platforms currently disagree:

| Provider | Peak / 忙时 | Off-peak / 闲时 |
|---|---|---|
| DeepSeek | 周一至周五 9:00–12:00、14:00–18:00 | 其余（含整个周末） |
| Volcengine Ark | 周一至周五 09:00–12:00、14:00–18:00 | 其余（含整个周末） |
| Tencent (原厂直供) | 工作日 9:00–12:00、14:00–18:00 | 其余（含周末全天） |
| Tencent (0731/0813 self-hosted) | 周一至周日 9:00–12:00、14:00–18:00 | 其余 |
| Aliyun Bailian | 此外为忙时 | 东八区 22 点至次日 8 点 |
| Baidu Qianfan (`DeepSeek-V4-Flash-0731`, not `deepseek-flash`) | 08:00–22:00 | 22:00–次日 08:00 |

The first three therefore agree, and Aliyun is the outlier: its cheap window is overnight only, so midday (12:00–14:00), evening (18:00–22:00) and the whole weekend cost double there but are off-peak everywhere else. Baidu happens to draw the same window as Aliyun, on a different generation of the model — the two are independent statements that happen to coincide, never a rule to apply to one another.

Three traps: a `；` joins clauses of one rule (Tencent states the weekday window and the weekend exemption in a single sentence), so sentences are split on `。` only; a rule quoted from a footnote without naming any model applies to everything the document lists; and a window stated in prose does not move a price — the band a row belongs to still comes from the cell, as described above.

## Parser maintenance

Identify rows by content — parsed prices, table headers, or section anchors — never by a hard-coded model-name prefix, family list, or document-name suffix. A vendor rename, a newly launched family, or a renamed pricing document must be picked up without a code change. Where filtering is unavoidable, prefer an explicit blocklist of non-model sections (see `NON_MODEL_SECTIONS` and `NON_MODEL_SECTION_IDS`) over an allowlist of model prefixes, and keep alias maps additive so they never drop an existing match.

A whole-catalogue scan reads each vendor once, so an adapter that already parses its
entire document exposes it through `catalog_records()` rather than being walked model
by model; `PriceSource` still provides a walking fallback so an adapter that only
knows how to answer a single model scans correctly without changes. Aliyun reads the
Qianwen model-market catalogue once per adapter instance and reuses those items for
listing, exact queries, descriptions carried with price records, and full scans.
Only a model that publishes time-band prices adds one read of its own public detail
page, because that page's tooltip is where the hours are stated. `CachedPriceSource`
caches a scan as a single `catalog` entry, not one per model.

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
