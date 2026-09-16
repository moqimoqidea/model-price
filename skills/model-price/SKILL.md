---
name: model-price
description: Find major AI models and compare current official prices across cloud platforms and first-party providers, or scan every catalogue for what changed since the previous scan. Use for model availability, versions, service modes, token or cache pricing, provider comparisons, and price-move or model-launch tracking involving Aliyun, Volcengine, Tencent Cloud, Baidu Qianfan, DeepSeek, Kimi, Zhipu, MiniMax, Xiaomi MiMo, OpenAI, Anthropic, Google Gemini, or xAI Grok.
---

# Model Price

Run the bundled script before answering. Show every returned provider, version, service mode, region, price condition, official source, and source status — and when a price is split by time of day, the window it covers.

```bash
# Search domestic providers. GPT, Claude, or Gemini names also add their relevant first-party provider.
python3 scripts/query_model_prices.py compare MODEL --format markdown

# Query selected providers, require an exact ID, or include all overseas providers.
python3 scripts/query_model_prices.py compare MODEL --provider PROVIDER --provider PROVIDER
python3 scripts/query_model_prices.py compare MODEL --exact
python3 scripts/query_model_prices.py compare MODEL --include-overseas

# Query or list one provider.
python3 scripts/query_model_prices.py provider PROVIDER MODEL --format markdown
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX

# Scan every catalogue and report what moved since the previous scan.
python3 scripts/query_model_prices.py delta --format markdown
python3 scripts/query_model_prices.py delta --provider PROVIDER --format json
```

Provider IDs: `aliyun`, `volcengine`, `tencent`, `baidu`, `deepseek`, `kimi`, `zhipu`, `minimax`, `xiaomi`, `openai`, `anthropic`, `google`, `xai`.

Do not query `openai`, `anthropic`, `google`, or `xai` by default. Add only the relevant provider when the user explicitly mentions GPT/OpenAI, Claude/Anthropic, Gemini/Google, or Grok/xAI; use `--include-overseas` when the user explicitly asks about overseas models generally.

Each provider caches lists and price searches independently under `cache/`. Fresh caches are valid for 3 hours. Add `--refresh` only when the user asks for the latest/current refresh; with `provider` or repeated `--provider`, refresh only those providers. `delta` is the exception — it always reads the sources afresh, because a cache hit would hand the previous scan back as "no change".

`delta` is the incremental run, for a scheduled or repeated check rather than a question about one model. It takes no model name: it scans each provider's whole catalogue and compares it with the baseline the previous run left in `snapshots/`. It covers domestic providers only unless `--include-overseas` is given, prints Markdown unless `--format json` is asked for, and runs the skill's own Git update check the way any refresh does. A baseline never expires, so a run a week later still compares against the last scan.

Report every scanned provider, including the ones that did not move — that is what shows the scan actually covered them. A first run reports `baseline_created` and how many models it recorded, instead of claiming nothing changed. `unchanged` is stated plainly as 模型无变化, and carries the vendor's own update stamp in the vendor's own wording when its catalogue publishes one. `changed` lists the models added with what they cost, the models withdrawn with what they cost before, the offers added or removed, and every price that moved, old amount to new. A provider that fails (`source_error`) or yields no priced model (`empty_scan`) is named with its reason and keeps its previous baseline rather than overwriting it, so the change stays visible once the source recovers.

Before any `--refresh` source request, the script fetches the skill repository's configured Git upstream. It fast-forwards and restarts with the updated skill only when the upstream changed this skill, the branch can fast-forward, and the working tree is clean. It otherwise continues with the current code and reports `skill_update` as `up_to_date`, `update_skipped`, or `check_failed`; never hide that status. If an overseas source is unreachable and no fresh cache exists, report `source_error` and say its price is unknown.

Use family matching for availability comparisons; use `--exact` only for an exact official ID. Never merge self-deployed, platform-hosted, third-party-hosted, upstream-direct, first-party, Batch, cache, context, service-tier, time-band, promotion, version, currency, or region variants.

Model identity ignores documentation footnote markers (`deepseek-flash(1)` == `deepseek-flash`). Officially retired names are mapped to their live model, so `deepseek-v4-flash` resolves to `deepseek-flash`; always report the `model_id` actually returned. A vendor that files the live model under its own spelling is also matched (`deepseek-v4.1-flash` and `deepseek-v4-1-flash` are the same model, so one query reaches every platform serving it), but that link is one-way: a live model never absorbs the price rows of a superseded generation. `--exact` insists on the literal official id either way. A model that is listed but priced per second, per request, or only on a free tier produces no price rows — say so rather than reporting a price of zero.

Table-driven adapters keep cache-hit and cache-miss prices apart (`输入（未命中缓存）` is regular input pricing, not cached-input pricing), and ignore headers that bill a non-token unit such as audio duration. A header that describes the request rather than a price — a length band like `条件 输入长度：千 token`, or a peak/off-peak label — is a condition, never a price column; reading one as a price silently drops the tier or time band that distinguishes rows. They read only amounts that name their own currency, so a page publishing both CNY and USD tables yields the CNY catalogue. Never relabel one currency as another.

Peak and off-peak hours are set per platform and must never be carried across them. For `deepseek-flash`: DeepSeek's own page, Ark, and Tencent's 原厂直供 series all peak on weekdays 9:00–12:00、14:00–18:00 with everything else — the whole weekend included — off-peak, while Aliyun instead discounts the overnight window 22:00–次日 08:00 and treats all of 08:00–22:00 as its 忙时. The two calendars therefore agree only on weekday mornings/afternoons and late at night; at midday, in the evening, and all weekend Aliyun is the odd one out and costs double. Each window is read from that vendor's own document into `time_bands`, quoted as the vendor wrote it, and repeated on every peak/off-peak row rather than left to a footnote. A platform that publishes no window stays empty — never give it another platform's hours. One page can bill two generations on different calendars (Tencent's 0731/0813 self-hosted builds keep peak at weekends, unlike the 原厂直供 series on the same page), so a rule is selected by model name first and by delivery label (原厂直供) second.

Prefer a representation the official page publishes for machines over scraping its rendered markup: the page's own Markdown copy when it publishes one with real Markdown tables, otherwise its public structured JSON. Parse rendered HTML only when the vendor publishes neither. Never use credentials or private console data.

Code layout: `scripts/query_model_prices.py` is the stable CLI entry point; the implementation lives in `scripts/model_price/`, with one module per vendor under `providers/` and shared concerns split into `core` (HTTP + source contract), `parsing` (document readers), `pricing` (record shapes), `models` (identity), `caching`, `updating`, `snapshots` (the baselines `delta` compares against), `diffing` (what moved between two baselines), `delta` (the scan-and-compare run), `reporting`, and `registry` (wiring). Add a provider by writing its module and listing it in `providers/__init__.py`; an adapter that parses its document in one pass overrides `catalog_records()` so a whole-catalogue scan costs one read instead of one query per model.

Read [references/schema.md](references/schema.md) when consuming JSON. Read [references/source-notes.md](references/source-notes.md) only when a source or parser needs maintenance.
