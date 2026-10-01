# Official sources

Prefer a representation the page publishes for machines over scraping its rendered
markup: its own Markdown copy when the tables are real Markdown tables, otherwise
its public structured JSON. Parse rendered HTML only when the vendor publishes
neither.

The two clouds that price a model per region — AWS Bedrock and Azure Foundry — are
each read for **one** region, the American one the vendor lists first, and the
record says which. A cloud price without its region is a price for nowhere, and a
scan of every region is a report nobody reads: the same model, the same charges,
thirty-five times over.

## Model announcements

`model_price/announcements/` reads official publication evidence independently
of price adapters and retirement notices. A publication can establish a model's
existence and capabilities before a public API, consumer product, or rate exists.
Conversely, a rate in the catalogue does not establish general access. Keep
literal published names until an exact normalized catalogue ID or display name
matches; never guess an ID, expand a series into variants, or use retired aliases.
No third-party host inherits its creator's announcement as a hosted listing.

| Channel | Discovery evidence | Reader and boundary |
| --- | --- | --- |
| Google Gemini | [Gemini blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/) and its [RSS](https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/) | Read release subjects from RSS, then official article JSON-LD and paragraphs. The feed can link related official Google sections; only the same HTTPS host is accepted. |
| OpenAI | [News RSS](https://openai.com/news/rss.xml) | Model subjects in release titles, then official articles at their canonical trailing-slash paths, avoiding one redirect per article. Typeset and nonbreaking hyphens are accepted when verifying a literal model mention; suffixed variants remain distinct. An inaccessible body retains verified feed evidence and reports partial `source_error`; customer stories without model-title subjects and bug-bounty campaigns do not discover models. |
| Anthropic | [News](https://www.anthropic.com/news) | Follow indexed official model-release links, including article paths outside `/news`. Scope Fable and Mythos facts separately within their shared article. |
| xAI | [News](https://x.ai/news) | Read card dates before following recent model subjects, so old indexed releases do not exhaust the request budget. |
| Aliyun Bailian | [Qwen blog](https://qwen.ai/) | Anonymous `GET /api/v2/article/retrieval?type=qwen_ai&language=en-US` returns `data.articles`: `title`, `path`, `content`, and `extra.date/description/introduction`. Public `blog?id=<path>` uses the published path, not the internal UUID. The old GitHub Pages blog announces its migration and is not a current substitute. Qwen research/open weights do not themselves prove Bailian hosting. |
| Volcengine Ark | [Seed models](https://seed.bytedance.com/en/blog) | Read `window._ROUTER_DATA.loaderData.layout.footer_config`, the group titled `Models`, and its `labelEn/linkEn`. This is operator research inventory evidence, not guessed Ark IDs. A name-only entry has an unavailable capability summary rather than invented prose. |
| Tencent TokenHub | [Hunyuan research](https://hunyuan.tencent.com/) | Anonymous read-only `POST https://api.hunyuan.tencent.com/api/blog/publicList`, paged by `pageNum/pageSize`; `needFilter=true`. Read the published `title/desc/content/customUrl`, and only official dates when present. This idempotent read is retryable. Public research evidence does not replace the authenticated TokenHub description mirror or prove a TokenHub listing. |
| Baidu Qianfan | [ERNIE blog RSS](https://ernie.baidu.com/blog/index.xml) | Official model-release subjects and full feed content. Research/Arena previews need not have Qianfan rates. An old feed cannot establish coverage of announcements absent from that feed. |
| DeepSeek | [Official updates](https://api-docs.deepseek.com/zh-cn/updates/) | Reuse `deepseek_updates.read_updates` and its official dated model entries, shared with introductions and retirement evidence. Do not treat an upcoming benchmark framework as a closed API. |
| Kimi | [Official blog](https://www.kimi.com/blog) | Official release cards, including navigation research links; follow only blog article paths. Upcoming weights and technical reports do not close an already usable Kimi API. |
| Zhipu | [Model overview Markdown](https://docs.bigmodel.cn/cn/guide/start/model-overview.md) | Reuse the introduction table parser's complete inventory, without price lookups or a model-name list. This covers the published capability inventory, not every research news source. |
| MiniMax | [Official news](https://www.minimax.io/news) | Official indexed model releases. Announced weights and API access remain different claims. |
| Xiaomi MiMo | [Model overview](https://mimo.mi.com/docs/zh-CN/quick-start/summary/model) | Reuse the existing overview reader; TTS entries without a price row are retained. Absence of prices never becomes a free rate or a claim of closed access. |
| Kling | [Video](https://klingai.com/document-api/guides/capability-map/video.md) and [image](https://klingai.com/document-api/guides/capability-map/image.md) capability maps | Reuse the introduction inventory for independent model/capability discovery. Both named models and officially named billable capabilities keep their literal entries. |
| Google Cloud Vertex | [Official release-note Atom feed](https://cloud.google.com/static/feeds/generative-ai-on-vertex-ai-release-notes.xml) | Dated bodies name the hosted models; evidence links use `docs.cloud.google.com`. The feed may lag the platform's current renamed product pages; do not claim its silence proves no release. |
| AWS Bedrock | [AWS What's New RSS](https://aws.amazon.com/about-aws/whats-new/recent/feed/) | Keep only model-subject entries whose title or summary identifies Bedrock. Original creator announcements are insufficient. |
| Azure Foundry | [Azure blog RSS](https://azure.microsoft.com/en-us/blog/feed/) | Keep Foundry/Azure model subjects and quoted body scope. A Limited Access Program remains limited even when the headline uses general-availability wording. WordPress's repeated “The post … appeared first on …” trailer is not release evidence. |
| OpenRouter | Its existing [models API](https://openrouter.ai/api/v1/models?output_modalities=all) | Explicit `catalogue_only` announcement coverage. Free, unpriced, and router listings already participate in catalogue scans; creator blogs never prove OpenRouter hosting. |
| Ant Ling | Its [first-party price catalogue](https://developer.ant-ling.com/zh-CN/docs/models/price/) | `catalogue_only`: pricing and retirement evidence are registered, but no independent publication reader is registered. A successful price read does not claim announcement coverage. |

News discovery uses a 90-day window; current capability inventories have no age
cutoff, and undated current-index releases are readable. The scope is independent
of character limits. Retained releases survive rolling-index omissions; newer
evidence cannot be replaced by an older repeat. The initial successful scan emits
first-observation causes. A missing historical baseline keeps comparison changes
empty but exposes new first observations once, so upgrading a historical monitor
does not silently absorb releases. Later comparisons detect new publications,
explicit access-status changes, announced-rate changes, and same-name catalogue
matching changes. Separate `snapshots/announcements-<provider>/` archives use
their own schema version and the existing historical selection/retention rules.
An empty first discovery or a partial/failed read writes no announcement baseline.
Price and notice baselines are unaffected.

Access is quoted per model and audience. Longest named variants own their clauses;
an anaphoric sentence can reuse the single subject of its own paragraph, never
another paragraph's subject. An early-access testimonial, restricted feature, or
future model weights do not establish the model's API status. A public API and a
private preview on another surface can coexist; retain their complete sentence.
Unknown stays unknown when the official evidence does not establish access.

Announcement token rates retain each independent sentence's original terms under
`announced_offers[].conditions.published_terms` and in `pricing_notes`. These are
quoted publication evidence, not current billed catalogue offers. For Argon, keep
the introductory $2/$10 and later $4/$20 input/output rates separately. Do not
calculate cache rates from “95% off” or create `effective_until` from “at least
through”. Unreadable prices are not zero. Full amounts, terms, summaries, and
source URLs survive message overruns. The dated [coverage audit](announcement-audit.md)
records current examples, live-read failures, and remaining source limits.

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
| Tencent TokenHub | [Product announcement index](https://cloud.tencent.com/document/product/1823/130758) and its linked [individual notices](https://cloud.tencent.com/announce/detail/2469) | Read exact `model` parameters or the literal ID in a labelled 下线模型 field, and prose or labelled 下线时间 schedules. Preserve seconds and separately stated redirect dates. Conditional migrations retain their qualification in `scope`; an announcement-time example of the latest model never becomes a fixed redirect target. Price notices alone are insufficient retirement evidence. Follow up to 17 unique notices, reserving the other three document reads within the default host budget; rolling omissions retain archived events. |
| Baidu Qianfan | [Retirement mechanism and history](https://cloud.baidu.com/doc/qianfan/s/zmh4stou3) | Historical table gives registration and retirement dates per hosted model, plus recommended replacement. Its example row is excluded. |
| Aliyun Bailian | [Deprecation policy](https://help.aliyun.com/zh/model-studio/model-depreciation) and public [model market](https://www.qianwenai.com/models) | The anonymous market API exposes per-model `OfflineInfo.Inference.OfflineTime`. Read it from the fresh catalogue; convert an explicit UTC instant to Beijing time, and keep an undated or missing value unknown. |
| DeepSeek | [Official updates](https://api-docs.deepseek.com/zh-cn/updates/) | One dated entry per release, newest first: `h2` carries `时间: YYYY-MM-DD` and each `h3` under it names a model. A route that does not exist answers HTTP 200 with the docs home page, and the site marks only one of the two spellings canonical, so a response counts only when it names the log. Record only explicit old-version withdrawal and continued routing stated in the changelog; there is no complete future retirement timetable. |
| Kimi | [Model list](https://platform.kimi.com/docs/models) | Retired-model section gives series-level dates and literal retired IDs. Match a table ID to the longest published series prefix. |
| Xiaomi MiMo | [Deprecation log](https://mimo.mi.com/static/docs/updates/deprecate.md) | Separates the earlier automatic replacement time from the final old-ID expiry time where both are printed. |
| Ant Ling | [Model deprecation](https://developer.ant-ling.com/zh-CN/docs/models/deprecation/) | Read tables headed 模型 ID with 计划下架日期 or 下架日期. 即将下架 is scheduled; 已下架 explicitly confirms retirement. Preserve the published UTC+8 clock to seconds and each literal ID's case. 推荐替代模型 is a manual migration recommendation, not an automatic redirect: the policy says old-ID API calls return errors after shutdown. 当前可用模型 and partner migration examples are not retirement notices. A partially unreadable notice table is a source error. |
| OpenAI | [API deprecations](https://developers.openai.com/api/docs/deprecations) | Published notification and shutdown tables. A row may name several aliases separated by escaped Markdown pipes; each literal ID gets its own event. |
| Anthropic | [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations) | Deprecated and retired table applies to Anthropic-operated API. A row explicitly marked Retired is down even if its retirement date is blank. Partner platform schedules may differ. Active models' “not sooner than” dates are not shutdown promises. |
| Google Gemini | [Gemini API deprecations](https://ai.google.dev/gemini-api/docs/deprecations) | Shutdown dates are the *earliest possible* dates. The page separately marks already-shutdown models with `row-gray` table rows, including one with no published shutdown date. Only that explicit row state confirms retirement; reaching an unshaded row's date does not. |
| xAI | [Official migration guides](https://docs.x.ai/developers/migration/may-15-retirement) | Old slugs can continue to resolve through automatic redirection, with billing at replacement-model prices. Read the effective PT clock where given; a date-only notice remains date-only. |
| Zhipu BigModel | [GLM-4.5-Flash](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.5-flash.md), [GLM-Z1](https://docs.bigmodel.cn/cn/guide/models/text/glm-z1.md), and [GLM-4.5](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5.md) model pages | No central retirement feed. Read explicit banner text on these known official pages: a dated shutdown with automatic routing, an undated “已下线” series notice, and undated “即将下线” plans. Only literal named IDs are recorded; a series notice is not expanded into guessed variants. These pages are a maintained source list, not a complete vendor-wide schedule. |
| OpenRouter | The models API entry itself | Each catalogue entry publishes its own `expiration_date` while the model is still served, so the date is an announced withdrawal rather than the catalogue going quiet. Read from the price records, like Aliyun's offline times. |
| Google Cloud Vertex | [Model versions and lifecycle](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions) | One table per modality: the literal model ID, its release date, its retirement date and what replaces it. A date the page qualifies ("July 21, 2027 or later", "No sooner than May 20, 2028") is a floor and is recorded as `eos_earliest`; "No retirement date announced" yields no event. |
| Azure Foundry | [Model retirements](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements) | A table of the literal model name, the version pinning a deployment, and the day its deployments stop. Only dates the vendor states are read: a training date published as a floor ("No earlier than 2027-04-01") is not a day anything happens, so it is not recorded as a milestone. |
| Kling | none published | The console announces a withdrawal rather than a page. Reported as `no_public_schedule`, which is the honest status rather than a failed read. |
| AWS Bedrock | none readable here | AWS files its dates on `docs.aws.amazon.com`, a host that does not resolve on every network this skill runs from; a reader would report a failed source on each scan instead of the truth that no schedule was read. Reported as `no_public_schedule`. |
| MiniMax | [Official model overview](https://platform.minimax.io/docs/guides/models-intro.md) | `Legacy Models` accordions identify old versions but provide no shutdown date and do not prove service cessation. A separate music notice explicitly discontinues three free music API IDs on 2026-08-20; the paid API restriction applies only to new users and does not retire those models. |

Run `python3 scripts/audit_model_retirements.py` to read registered vendor-specific
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
The resolver selects only the hosting provider's registered reader and groups by
provider ID plus the normalized literal model ID. It never infers a reader from a
model's family, falls back to its creator, or merges introductions across channels
or retired-name aliases. Query failures are isolated from prices; `delta` resolves
only models present in an actual catalogue, notice, or publication change. The same model on two
platforms keeps each platform's own prose and evidence. Baidu, Vertex AI, AWS
Bedrock, Azure, and Ant Ling currently have no registered catalogue-introduction reader: they report
`not_found` with their channel reference page, rather than borrowing a first-party
introduction. Retirement evidence is independent and never substituted for prose. Verified
announcement prose is passed directly from that channel's registered release
source, without guessing a per-model API documentation URL.

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
- OpenRouter describes every entry it lists from its own catalogue: the paragraph
  the API publishes, plus the modalities, the parameters it accepts and the limits
  it states. An entry the vendor charges nothing for says so among those
  capabilities ("官方公布价格为 0（免费档位或测试期），未作为价格记录") — which is
  where that fact belongs, because it is a property of the listing rather than a
  rate, and because a model priced at zero and a model with no price are the same
  number and two different things. The video document is consulted only for an
  entry whose own rates are all zero, so a priced model never pays for it.
- Kling publishes no per-model page. Both of its capability maps (video and image)
  are read instead: the vendor's own words for each model, what it takes in, how
  long a clip it makes and at what resolution. The same pages describe the
  platform's own chargeable capabilities (扩图, 数字人, 对口型), which the price page
  prices under exactly those names.
- DeepSeek uses the official update log (`deepseek_updates.py`): the same dated
  entries serve the price stamp, the retirement audit, and the introductions, so
  none of them has to guess a date-shaped news URL per release. An entry is
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

Batch price links use the adapter's own `catalog_url` when present, otherwise its
price source URL. OpenRouter therefore links its public `/models` page rather
than its API. This is runtime report metadata, not part of snapshot identity.

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
  `list_amount` and `discount` for auditability. The market page renders the same
  relation in words and prints both numbers — `输入（Batch Chat) 限时5折 ¥ 12 ¥ 6`,
  `视频生成（480P）限时7折 ¥ 0.3 ¥ 0.21` — so `Price` is the 刊例价 and the `Discount`
  count is the 折 number (0.5 is 5 折). The sibling `input_token_batch` row publishes
  the same discounted figure already flattened (6 beside the 12), which is what
  cross-checks the multiplier reading. The page says 限时 but the API states no end
  date, so no window is recorded: a period is written only where the vendor published
  one. `BuiltInToolMultiPrices` describes
  optional tool calls rather than model inference, so those charges are not folded
  into a model's token or generation offers. Model detail URLs percent-encode the
  literal model id; their server-rendered tooltip is the official source for the
  peak/off-peak window. Catalogue fields also supply the model introduction,
  capabilities, modalities, limits, update time, and scheduled withdrawal.
  A listed preview may publish no charge or only a zero placeholder; it remains
  a catalogue model with unknown price. The public `Name` can say `Preview` or
  `预览版` even when `VersionTag` says `MAJOR`, so the displayed label also supplies
  preview lifecycle evidence.
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
- Tencent Cloud TokenHub: the document UI's anonymous, read-only JSON API at
  `https://cloud.tencent.com/document/cgi/document/getDocPageDetail`. POST a JSON
  object with `action: getDocPageDetail` and `payload: {id, lang: zh,
  isPreview: false, isFromClient: true}`; no account, cookies, or CSRF code is
  supplied. The catalogue and prices use their own article IDs, `130051` and
  `130055`, while the retirement-link index uses `130758`. Validate `code: 0`,
  `data.categoryId`, and `data.content`; prices read `content.slate`, and the
  retirement index reads the published HTML in `content.body`. Both come from
  the same article object the UI renders. Preserve self-deployed and “原厂直供”
  rows, the canonical document source URLs, and `content.recentReleaseTime` as the
  official price update time. Each article is fetched and decoded once per run.
  There is no Markdown endpoint: the "MD" button converts this same Slate data
  in the browser with remark.
  On 2026-10-01, the anonymous API's catalogue content equalled the HTML's embedded
  article exactly. The older hydration reader's “document state was not found”
  message alone cannot distinguish a partial page, intermediary/challenge, or
  changed markup. Reading the UI's JSON removes that HTML dependency. Invalid
  responses retain the API URL, document URL, and response size; unreadable notice
  indexes and unparseable milestones remain distinct failures, never empty
  successful scans. Ordinary responses are not retried solely for parser errors.
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
  written into the item's own text (`输入（高峰时段：8:00-22:00）`). A fourth axis is
  time: an activity announces its own name and window in a banner above the tables,
  and prices itself either in a cell that labels its two rates (`原价：0.002</br>国庆限定价：0.0012`
  — the vendor writes the break in its closing form) or in a column that names it
  (`批量推理 （2月活动价）`). Each rate is its own offer: the standing one is named
  after the channel, the promoted one after the activity, and only the latter
  carries `channel` and `promotion_window`. A rate whose window has run out is not
  read, and neither is a column naming an activity the banner does not carry —
  the page keeps columns of activities it has stopped running and dates them
  nowhere else. Prices are quoted per thousand tokens and restated per million. The
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
  update stamp, so it takes the newest date the official update log publishes
  (`时间: 2026-09-10` today). Reading the log is what keeps this stamp, the
  retirement audit, and the introductions on one source instead of three. For a
  provider with no official stamp at all, the message uses the previous successful
  snapshot time and labels it 上次更新时间.
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
- Ant Ling: `https://developer.ant-ling.com/zh-CN/docs/models/price/`. The public
  Nextra page renders complete tables and publishes no Markdown/JSON download
  link. Read its first-party CNY tables once; exclude the entire 第三方平台 heading
  path so OpenRouter/ZenMux rates or partner-only models cannot become Ant offers,
  even if a partner later quotes CNY. The 模型 column identifies rows; 定位 stays
  a condition. Input, output, and cache reads are quoted per million tokens.
  HTML `<del>` amounts survive as list prices, and the adjacent amount is what
  is billed. Model-scoped promotion sentences retain the published discount and
  gradual-return wording in `pricing_notes`; no exact expiry or price-level
  multiplier is inferred from that prose. Hydration scripts are not quoted as
  prose. The labelled `Last updated on` date supplies `source_updated_at` at its
  visible day precision, without adding the hidden metadata's time of day. Both
  pricing and retirement use the shared browser identity, pacing, retry policy,
  and source-error diagnostics, with no extra transport or credentials.
- OpenAI: `https://developers.openai.com/api/docs/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Anthropic: `https://platform.claude.com/docs/en/about-claude/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Google Gemini: `https://ai.google.dev/gemini-api/docs/pricing.md.txt`. The page
  publishes a Markdown copy, so its `pricing-table` HTML classes are no longer read.
  DevSite can redirect a browser UA into OAuth even for this public download.
  The shared client retains only the run's anonymous server-issued cookies,
  stops before OAuth/sign-in, and retries the public URL once with the same
  browser identity. Those status cookies let DevSite serve the public document
  without starting another automatic sign-in attempt. The retirement page shares
  that anonymous session; no browser cookies, account, or persisted session is used.
  A model section is an `h2`, its service tiers are `h3`, and the section's API ids
  are published on an italic link line (`*[`gemini-3.8-flash`](url)*`), which is a
  better model id than the heading slug the HTML carried. Sections that price tools,
  agents, or general notes are excluded by name. Google is the vendor that dates a
  rate inside the price cell itself: the paid-tier column carries two dated amounts
  (`$0.75 through December 31, 2026. $1.50 starting January 1, 2027.`) across 90
  prices in 7 models, and its cache rows carry only the closing half
  (`$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.`).
  Both halves are read into the price's own terms — the day the amount stops and the
  rate that takes over — because the current rate is itself the promotion and the
  table publishes no list price to strike through. Every pair doubles, which is what
  a reading of the pairs is checked against. The cell's sentence stays in `display`.
- xAI Grok: `https://docs.x.ai/developers/pricing.md`. The rendered HTML page splits the text-pricing header across two rows with merged cells, which the shared table reader cannot align; the official Markdown keeps a single header row. Long-context billing tiers live in a trailing parenthetical in the model cell (`grok-4.6 (≥ 200k prompt tokens)`), so they are preserved as a `context_tier` condition rather than dropped with the model id.

### Special prices, and where each vendor states them

A price is not always one number. A vendor may publish the rate it reduces, a
multiplier, and the day its rate stops. Each is read where that vendor stated it, and
only where it stated it: a period is never inferred from a neighbouring vendor, and
prose is never turned into a date.

Read, because the vendor states them as data beside the amount:

- **Aliyun** — `Discount` multiplier, plus the 刊例价 it multiplies. See its entry above.
- **Baidu** — the activity banner's own name, window, and 原价/活动价 cell labels.
  See its entry above.
- **Google** — two dated amounts inside the price cell. See its entry above.
- **MiniMax** — struck-through list amounts (`~~4.20~~ 2.10`) are read as
  `list_amount`, so both numbers reach the report. The row's badge says 永久五折, but
  no `discount` is recorded: both amounts are already published, the ratio is
  arithmetic between two printed numbers rather than a figure the vendor states
  separately, and deriving one would put a number in the report the row itself does
  not print for that price. The badge is kept verbatim in the row's `context_tier`.
- **Ant Ling** — struck-through HTML list prices and adjacent billed amounts use
  the same shared rule. Promotion sentences remain model-scoped `pricing_notes`,
  including their discount and unspecified end period; see its source entry above.

Published, but only in a sentence beside the table — the sentence is kept verbatim as
the record's `pricing_notes` and the price stays the rate billed today. A sentence is
not a field: "at least through November 21, 2026" is not the same fact as an end date,
and 达到用量上限后恢复按刊例价结算 is not a multiplier, so nothing is parsed out of one.
A note is kept only when the sentence names the model, matched against the whole
catalogue at once so that a sentence about `GPT-5.6 Sol` is not also read as being
about `gpt-5.6`:

- **OpenAI** — `GPT-5.6 Sol's promotional pricing is available at least through
  November 21, 2026.` The table publishes the promotional rate and no list price, so
  this sentence is the only place the price is said to be temporary. It reaches
  `gpt-5.6-sol` and no other model.
- **Volcengine** — `Seedance 2.0 mini 与 Seedance 2.0 fast 现已开启限时优惠活动…达到规定
  的 token 用量上限后将恢复按刊例价结算`. Both models are video generators, and the video
  tables are kept out of the catalogue because they bill per token of generated video
  rather than per token of inference. There is therefore no record for the sentence to
  attach to. When that table is read, its cells state the promotion structurally
  anyway (`输入不含视频：原价 37.00 ``限时75折```), which is the same shape Aliyun and
  Google publish and belongs with `list_amount` and `discount` rather than with a note.
- **xAI** — `Batch discounts by model: **20% off standard rates**` heads a list of four
  grok models. The sentence names none of them, and the prices it discounts are Batch
  prices, which this document does not publish at all (the page asks the reader to
  toggle them on a model's detail page). A sentence naming no model is attached to
  none, so xAI carries no note.
- **Xiaomi** — `缓存写入：限时免费` and `mimo-v2.5-tts* 限时免费`. The first is preserved
  through the free wording; the second prices models the catalogue does not list.

Vendors with none of it: DeepSeek, Tencent, Kimi (its 限时免费 is a file API rather
than a model rate), and Anthropic (its discounts are negotiated per account, and its
geography multiplier is a term of service rather than a published price).

Provider caches live under `cache/<provider>/`. A cache entry records its provider, operation, arguments, fetch time, schema version, and data. Entries older than 3 hours are not used as fallback when refresh fails, and `CACHE_SCHEMA_VERSION` is bumped whenever a source or parser changes so entries written by an older version are ignored.

All source reads pass through the standard-library `HttpClient`. The shared
browser identity is the user's Chrome 154/macOS profile: its exact UA, Chromium/
Google Chrome client hints, desktop/macOS flags, English/Chinese language weights,
DNT, no-cache fields, and storage-access hint. There is no alternate-UA negotiation
or project suffix. Accept defaults to `*/*`; gzip/deflate are the advertised
encodings this standard-library reader can decode. Source reads use their own
origin as Referer with `empty`/`cors`/`same-origin` fetch context; an API can supply
its actual referring document and Content-Type. Redirect context follows the
destination. The example Google Tag Manager script's Referer and script/no-cors
context do not describe these document/API reads. Header overrides are
case-insensitive.

Only anonymous cookies issued during this run are held in memory. No browser
cookie store is read and no session is persisted. Redirects into authentication
hosts or sign-in paths stop before following them and omit the destination's query
state from errors. A safe public read can retry its original URL once with the
unchanged identity and its anonymous status cookies, then fails if authentication
is still requested. Unsafe POSTs never receive this retry.

Each host's request starts are at least one second apart, including retries and
redirects, and each host has a 20-attempt budget per run. GET/HEAD and explicitly
idempotent POST reads use at most three attempts for connection failures,
incomplete reads, damaged compressed responses, and HTTP 408, 425, 429, 500, 502,
503, and 504. Equal-jitter exponential backoff has a two-second base and a
30-second cap: the first waits are 1–2 seconds and 2–4 seconds. A valid Retry-After
on an error response is a minimum delay, never a replacement that shortens this
backoff. A requested wait above 30 seconds stops that host for the run; exhausted
429 retries do the same. Persistent verification pages, including those served
with HTTP 200, get at most one retry before that host stops. Ordinary permanent
4xx errors fail immediately and do not close other public documents on that host.
Other hosts continue normally.

POST is one attempt unless its caller declares a read-only operation idempotent;
the Bailian catalogue, Tencent document API, and Tencent research index do so.
Safe responses and failures are held only in run-local memory, keyed by method,
URL, body, and caller headers. Failures remain errors, never stale successes;
the next run tries the source afresh. Parser failures alone do not trigger new
requests. Every registered provider builds its catalogue in one source pass.
Diagnostics retain the source URL and distinguish timeout, connection failure,
rejected requests, verification pages, and server errors.

The self-updater's idempotent Git fetch uses the same UA and browser identity
headers while Git owns its protocol's Accept/content headers. It inherits system
proxies as process-local defaults, preserving explicit environment/repository
proxy preferences and existing process configuration without editing Git settings.
It retries transient transport failures, timeouts, and retryable HTTP statuses up
to three times with the same exponential backoff. Authentication/configuration
errors and local Git mutations are not retried.

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
  DeepSeek contributes the newest date its own update log publishes. For Kimi,
  Zhipu, MiniMax, and any other source with no official
  stamp, the message uses the previous successful snapshot's `captured_at` and
  labels it 上次更新时间. An absent official stamp never means the prices are stale.

### Vendors whose prices are not in a table

- **OpenRouter** — the models API (`/api/v1/models?output_modalities=all`) plus the
  video API (`/api/v1/videos/models`), one request each. The ids beginning with `~`
  are the vendor's own alias entries and repeat their target's prices under a second
  id, so they are left out; `:batch` is a billing mode of the model whose
  `canonical_slug` it shares and becomes an offer named `batch`; `:free` is a
  catalogue entry of its own. Every rate is per token and is restated per million
  tokens — the vendor's own model pages render them multiplied by a million and
  labelled `/M tokens`, and `image`/`image_output` are per image *token* on the same
  evidence. `image` is per picture only on a model that also publishes
  `image_token`, where the two answer different questions (0.01 per picture beside
  0.0000096 per image token). `web_search` is a charge per search and stays one.
  `pricing.overrides` becomes offers of its own: an entry with `min_prompt_tokens`
  is a long-prompt tier, and the entries tiling the day with `utc_start`/`utc_end`
  are the day's windows — the top-level prices are whichever window is running, so
  offering them beside the windows would move a price every time the clock crossed
  one. A video rate is published only in the video document, in cents or dollars
  per second depending on the SKU key, and the key names which: `cents_` figures are
  converted to dollars, `video_tokens` keeps its own measure. Negative figures are
  the vendor's marker for a charge that depends on the routed model and are never
  amounts.
- **AWS Bedrock** — one region per model, read from the two public offer files
  (`AmazonBedrock` and `AmazonBedrockFoundationModels`), read together because
  neither is complete: the first prices the models under AWS's own offer code, the
  second the ones billed through Marketplace, and only three names overlap. The
  pricing page itself is not read — its tables are JavaScript placeholders. Each
  record states the model, the region, the charge, the unit and the amount, so a
  rate per thousand tokens is restated per million and a rate per image or per hour
  keeps its own unit. Marketplace names carry an "(Amazon Bedrock Edition)" suffix
  that comes off. A charge whose own name says `batch`, `priority`, `provisioned`,
  `reserved`, `custom`, `tuning` or `storage` is a tier beside the standard rate,
  not part of it. One charge published under several SKUs is stated once. AWS
  publishes no model ids in these files, so the model name is the identity.
  **One region is read**, because the list prices a model in every region AWS serves
  it from and a report of thirty-five regions is not a price a reader can use: US
  East (N. Virginia) first, then the other American regions in the order AWS lists
  them, then GovCloud, and — for a model AWS serves nowhere in the United States —
  the region it does publish. The record names both the region read and its code, so
  an amount is never quoted without the region it is for. A price change in another
  region is therefore not reported; read that region by changing the declared order.
- **Microsoft Azure Foundry** — the OpenAI page, the Foundry Models pages, and
  Claude. The Foundry Models landing page names its sibling pages in a tab strip, so
  the serverless catalogue is discovered from that strip rather than listed in code:
  the set of vendors Azure prices changes, and the alternative is a catalogue this
  tool silently does not cover. Two rewrites make those pages readable at all: their
  amounts are a per-region JSON attribute on a ``$-`` placeholder the browser fills
  in, so the figure one region yields is written back where the placeholder was (the
  sign included — an amount naming no currency is not a price this tool reads), and
  each table's ``aria-label`` becomes the heading the shared reader looks for. One
  table declares a ``rowspan="2"`` model header and publishes a single header row;
  the span covers nothing and is dropped, because the shared reader carries a spanned
  cell into the rows it covers and the header would otherwise land on the table's
  first model. One region is read, as for AWS: East US when a page prices it, else
  the first American region the page names. Reservation columns — "Per PTU Hourly
  pricing", "Price (Unit/Hour) Monthly Commit" — are not read: capacity is not what
  this tool compares, and read as a use price a monthly reservation would be quoted
  as the cost of a request. A model the page lists without quoting a figure keeps its
  place with no price, which is what the page published.
  Claude has no Azure price page at all: the Foundry page publishes the catalogue and
  the deployment name, and states that a deployment is billed at Anthropic's standard
  rates, which are read from the Anthropic page that publishes them. The US Data Zone
  deployment type adds the multiplier that same page states (1.1x), recorded as a
  second offer carrying the rate it is a premium on. The model keeps the name it is
  published under rather than the deployment name beside it, which spells the version
  in dashes ("claude-opus-5-5") where the model's own id carries a dot.
- **Google Cloud Vertex** — one request to the Agent Platform pricing page, which is
  where `vertex-ai/generative-ai/pricing` now redirects. Three things about the
  markup: a delivery tier is a *tab*, so each `role="tabpanel"` is rewritten as a
  heading carrying a marker this adapter takes back off, and a tab that names a
  place (Global, `us-east5`, `europe-west 1`) becomes the region rather than a tier;
  a cell that stacks two values writes each in its own `<p>`, so the paragraph
  boundary is spelled as the line break the shared reader knows — without it
  "Gemini 2.5 Pro" and "Computer Use-Preview" arrive glued into one id; and the
  price columns name their own unit or none at all. A column that names tokens is
  read per million; the context-cache storage column states its rate per token-hour
  in the header ("Price Tok/hr<= 200K input tokens") and is restated per million
  tokens per hour; a column naming no unit this tool reads keeps its amount as
  published rather than being given a token rate the page never put there. `Input`
  and `Output` head the modality of a media row, so they stay conditions — unless
  their cells hold money, in which case the shared reader prices them.
- **Kling** — the image and video price pages, one request each, both real Markdown.
  A price is billed in the vendor's own credits, and the yuan beside each credit
  rate is that rate restated rather than a second charge, so the credit is the unit
  kept. Each page's model column also carries the platform's own categories
  (通用, 数字人, 对口型, 音频生成, 图像识别), which the vendor prices exactly as it
  prices a model; they stay, named as the vendor named them, with the function a row
  charges for as the offer it is bought as.

### Billing units each vendor publishes

The reader is unit-agnostic: what a table bills against is read from the vendor's
own wording, so a model priced per image or per second is read the same way as one
priced per token. The tables that carry those units:

- **Volcengine** — 视频生成（按 token 单价，在线推理／离线推理两种交付）、图片生成（元/张，
  输入图与输出图分开）、3D 生成（元/次）。The video token table publishes four tiers inside
  one cell and the image table six; each tier keeps its scope. The 价格示例 tables are
  **not** read: they restate the token rate as an example at one resolution and
  duration, and reading them would put a derived per-video figure beside the rate
  the model is billed at. Fine-tuning and capacity tables (精调、模型单元、套餐、插件)
  price training or hardware rather than a model's use, and are left alone.
- **Zhipu** — 多模态生成（元/次）、语音模型（元/万字符、元/次、元/分钟）、向量模型与其他模型
  （`单价（元/百万 Tokens）`, which the old header rule missed because it does not say
  输入/输出). Search tools, the knowledge base, private-deployment packages, and the
  fine-tuning/private-instance sections are not model prices.
- **MiniMax** — 语音合成（元/万字符）、视频（元/秒、元/张、按 token 的再生成）、历史视频
  （元/视频）、音乐（元/首）、图像（元/张）、MCP 的 `API-vlm`（元/次）. A service tier is
  stated by the tab a table sits in and a legacy section by its accordion, so both
  are turned into headings the shared reader files the table under. The ASR table
  names an interface rather than a model, and 音色管理 prices a capability, so
  neither is a model.
- **Xiaomi** — ASR 系列（`输入音频时长`, 元/小时 in the cell). The TTS models are priced
  in a sentence rather than a table (`…限时免费`) and are not read.
- **Ant Ling** — 输入、输出与缓存读取（人民币/每百万 token）. Third-party platform
  tables do not supply this first-party channel's offers.
- **Tencent** — the whole page is walked in order, so every product line is read:
  图片生成（元/张）、视频生成（元/秒、元/张）、3D（元/个）、语音（元/万字符、元/秒、元/首、
  元/音色）、积分计价的视频与 3D（积分/次、积分/秒）. A credit price keeps the credit
  as its unit, and the sentence the page writes above that table saying what a
  credit is worth ("1积分对应0.12元") travels as the record's `pricing_notes`
  rather than being converted. A column whose unit this tool cannot read is left to
  the page, which is what keeps reserved throughput (`元/kTPM/月`) and capacity out. The tabs that name a region are kept apart as a `region`
  condition: one model sold in 广州 and 新加坡 at different rates is two offers. The
  legacy comparison tables (`旧计费方式` beside `新计费方式`) are skipped, because the
  generation that replaced them has its own billing table.
- **Baidu** — 图像生成与图像编辑（元/张）、OCR（元/页）、文本向量与重排序（元/千 tokens,
  restated per million）. Those tables carry no version or channel column, so they
  are read by a second shape that finds its price columns by what their cells
  publish, with the row's own 单位 column as the evidence of currency.
- **xAI** — `### Imagine Pricing`: 每张图与每秒视频, both stated inside the cell.
- **OpenAI** — the page files its tables under a line of its own rather than under a
  heading, and every section but the first four carries the same generic heading, so
  the words published above a table are what it is read under and a label naming a
  service level is that table's tier (the tier vocabulary is read from the page's own
  `... pricing data` headings). The image, realtime/audio, transcription, and
  specialized tables are read this way, with the modality, use case, or category
  filed as a condition. The tools table names a tool in its first column and the
  fine-tuning tables head a training charge, so neither prices a model's use.
- **Google** — a family lists its API ids on one italic line and prices its variants
  one row at a time ("Veo Test Fast …" for `veo-test-fast-generate`), so each row is
  matched to the id whose own name carries a word the sibling ids do not; the id with
  no such word takes the row that carries none. A section whose rows cannot be
  matched one-to-one is left whole rather than guessed at. Only a column billed in
  something other than tokens prices variants: a token column's rows say which charge
  they price. A rate the page restates in another unit is kept in both units — in
  brackets beside the token rate (`$6.50 ($0.00016 per second)`), as a whole-cell rate
  (`$0.039 per image`), or as a stated equivalent (`Equivalent to $0.045 per 1K
  image`), whose resolution stays in the price's label.
- **Kimi** — the batch document is read with the chat document and its `（Batch）`
  suffix is a service level rather than part of the model's id, so one model sold two
  ways is one record with a standard offer and a batch offer. The other sibling
  documents price tools, sandboxes, plugins, and account tiers.

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

## Network reachability

Every source above is credential-free, but not every host resolves everywhere this
skill runs. On a mainland-China network `docs.aws.amazon.com` does not resolve at
all (`curl` exit 6), while `pricing.us-east-1.amazonaws.com` and
`aws.amazon.com` do — which is why Bedrock's prices are read from the offer files
and why AWS publishes no retirement schedule here. Two vendors' documentation pages
(`klingai.com`, `cloud.google.com`) serve fine. A source that cannot be reached is
reported as `source_error` rather than being quietly dropped; `snapshots/` keeps the
last successful baseline so the change is still visible on the run after it recovers.

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
- Read only amounts that name the adapter's own currency (`CNY` tables emit CNY, `USD` tables emit USD). Never relabel one currency as another.
- A price column is one whose header names a charge **or** whose cells publish amounts in the adapter's own currency. The second reading is what admits a column headed `输入音频时长` whose cells are `¥0.5 /小时`: the header names the billed quantity and the cells name the money. A column that names a unit whose cells hold durations is a condition, because nothing in it is a price.
- A header that describes the *request* instead of a price is a condition, and it is what keeps a duration column from being read as a money column. Length bands are the trap: `条件 输入长度：千 token` contains 输入 and names a unit without pricing one, so an input-price rule would swallow the column and silently drop the tier that separates otherwise identical rows (see `REQUEST_MARKERS` / `describes_request`). The cell value, not the heading, decides a time-band key, because vendors file the peak/off-peak split in the same generic 条件 column.

Conventions that come with the document readers:

- Vendor Markdown is escaped (`deepseek\-v4\-flash正式版`, `输入长度 \[0, 32K)`). The reader removes those escapes before a cell is named, matched, or compared, and a row keeps its empty leading cells so a table with a carried model name stays column-aligned.
- A table is read with the whole heading path that precedes it, so a `h2` model (`Gemini 3.8 Flash`) and the `h3` tier under it (`Standard`) stay distinguishable.
- A cell break (`<br>`) survives both readers as written, so the values a vendor stacks in one cell stay separable: the first is the model, the rest are variants the same price covers (`ERNIE-5.0<br>ERNIE-5.0-Thinking-Preview`). A note stacked under a model name (`调整前价格，2026-08-21 起不适用`) is preserved as a `model_note` condition instead of being dropped, because the price on that row no longer applies.
- HTML deletion markup (`del`, `s`, `strike`) survives as Markdown `~~` markers.
  A struck amount is a list price beside the billed rate, never a second charge;
  both amounts must retain their own currency. Hydration scripts and styles do
  not supply quoted promotion prose or labelled update stamps.
- Cells are laid onto a real grid, repeating a value across every row a *row span* covers — a vendor writes such a cell once, and without the repeat the columns below it shift left and a price lands under the wrong heading. A *column span* is deliberately not repeated: it is how a vendor lays a row heading across the columns beside it, and expanding it would fill the header row — the row that says which columns are prices — with copies of the heading. A cell arriving with no open row starts one, because Baidu's own table drops one `<tr>` and those cells belong to that row, not to the one above.
- A vendor that quotes a rate per thousand or per ten thousand tokens is restated per million (`tokens_per_price_unit` / `per_million_tokens`), so one report compares one unit. The figure the vendor published stays in the price's `display` text. A label that does not price tokens at all (`元/页`, `元/次`) keeps its own unit and is never rescaled: a page rate restated per million tokens would quote a number nobody charges.
- A price is read with the unit the vendor billed in, and every reading of unit wording lives in `pricing.UNIT_MEASURES` (see `references/schema.md` for the codes). A unit this tool cannot read is not a reason to drop the model: the model stays in the catalogue and the vendor's own wording is kept, either as the price's unit or, where the vendor stated no unit, as `provider_defined` rendered as the amount alone.
- A bare number is read as money only where the clause published nothing else and the header (or the row's own unit column) named the currency. A number embedded in words is never a price: a scope line reading `输出视频分辨率为 1080p` states a resolution, and taking its digits would price the model at 1080.
- A cell that publishes several tiers states each tier's scope in the cell, and each tier becomes its own offer under `conditions.price_scope`. A cell that lists several model IDs (`image-01<br />image-01-live`, `speech-2.6-hd / speech-02-hd`) prices each of them, one model each.
