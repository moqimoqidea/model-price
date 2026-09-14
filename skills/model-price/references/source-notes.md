# Official sources

Prefer public JSON used by the official page; otherwise parse the official Markdown or HTML listed here.

- Aliyun Bailian: anonymous model-center JSON API in the script.
- Volcengine Ark: catalog and prices from `https://docs.volcengine.com/docs/82379/1544106`, using its public `getDocDetail` JSON and `Result.Content` rather than `MDContent`.
- Tencent Cloud TokenHub: embedded JSON from the official catalog and pricing documents; preserve self-deployed and “原厂直供” rows.
- DeepSeek: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`; the model table is keyed by a `模型` header. Model columns carry footnote markers such as `deepseek-flash(1)`, so markers are stripped before matching. The current model is `deepseek-flash`; retired names (`deepseek-v4-flash`, `deepseek-v4-flash-vision-exp`) resolve through `RETIRED_MODEL_ALIASES`.
- Kimi: `https://platform.kimi.com/docs/llms.txt` indexes the chat pricing document as `pricing/chat.md`; dated variants such as `chat-k3.md` have also been served, so the whole `chat*` family is matched. Rows are JSON arrays shaped `[model, unit, cache hit, cache miss, output, context]`. The sibling documents (`batch`, `tools`, `limits`) are not per-model token tables and must stay out of the catalogue.
- Zhipu BigModel: anonymous configuration API defined in the script.
- MiniMax: `https://platform.minimaxi.com/docs/guides/pricing-paygo.md`, split into the language-model and speech sections.
- Xiaomi MiMo: `https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go`. The table's first column is the product line (`MiMo-V2.5 系列`), not a generic `Model` header, so the model column falls back to the leftmost non-price column. The page publishes the same models twice — `模型国内定价` in CNY and `模型海外定价` in USD — and only the CNY tables are read. The ASR table (billed per audio hour) and the plugin pricing section are not token pricing and are skipped.
- OpenAI: `https://developers.openai.com/api/docs/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Anthropic: `https://platform.claude.com/docs/en/about-claude/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Google Gemini: `https://ai.google.dev/gemini-api/docs/pricing`; no public pricing JSON or Markdown representation is exposed, so parse its server-rendered pricing tables and paid tier.
- xAI Grok: `https://docs.x.ai/developers/pricing.md`. The rendered HTML page splits the text-pricing header across two rows with merged cells, which the shared table reader cannot align; the official Markdown keeps a single header row. Long-context billing tiers live in a trailing parenthetical in the model cell (`grok-4.6 (≥ 200k prompt tokens)`), so they are preserved as a `context_tier` condition rather than dropped with the model id.

Provider caches live under `cache/<provider>/`. A cache entry records its provider, operation, arguments, fetch time, schema version, and data. Entries older than 3 hours are not used as fallback when refresh fails.

## Parser maintenance

Identify rows by content — parsed prices, table headers, or section anchors — never by a hard-coded model-name prefix, family list, or document-name suffix. A vendor rename, a newly launched family, or a renamed pricing document must be picked up without a code change. Where filtering is unavoidable, prefer an explicit blocklist of non-model sections (see `NON_MODEL_SECTION_IDS`) over an allowlist of model prefixes, and keep alias maps additive so they never drop an existing match.

Two conventions apply to every table-driven adapter:

- Keep cache-hit and cache-miss prices apart. A cache miss is billed at the regular input rate, so `输入（未命中缓存）` must not be classified as cached input.
- Read only amounts that name the adapter's own currency (`CNY` tables emit CNY, `USD` tables emit USD) and ignore headers that bill a non-token unit such as audio duration (`输入音频时长`, per hour) or per-request pricing. Never relabel one currency as another.
