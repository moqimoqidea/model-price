---
name: model-price
description: Find and compare major AI models by purpose, capabilities, specifications, lifecycle exceptions, availability, version, service mode, and official price, or scan catalogues and official retirement notices for launches, withdrawals, announcement dates, EOM, automatic redirects, EOS, billing changes, and price moves. Use for model research, provider availability, pricing, and change tracking involving Aliyun, Volcengine, Tencent Cloud, Baidu Qianfan, DeepSeek, Kimi, Zhipu, MiniMax, Xiaomi MiMo, Ant Ling (蚂蚁大模型), OpenAI, Anthropic, Google Gemini, xAI Grok, or Mistral AI.
---

# Model Price

Query public official model catalogues, introductions, prices, releases, and
retirement notices without credentials. Use the bundled script for the facts;
never answer a current price or change question from memory.

For **daily IM reports**, read [references/daily-workflow.md](references/daily-workflow.md)
before acting. It owns installed-version verification, a single scan, concise
report preparation, staged delivery, and the separate repair/publish gates.
Apply recipients, timezone, baseline, and confirmation tokens from the user's
request; a request to inspect a report does not authorize sending it.

Read [references/reporting.md](references/reporting.md) when composing or forwarding
a message. It owns report order, protected facts, batching, summarization, and the
shorter-delivery digest rules. Produce the finished concise report directly; do not
offer an optional second compression pass.

## Choose the command

Run from the actual installed skill directory; resolve symlinks to its Git clone.
A development checkout or temporary copy is not the installed skill.

```bash
# Named-model questions: domestic channels, plus an inferred first-party vendor.
python3 scripts/query_model_prices.py compare MODEL --format message
python3 scripts/query_model_prices.py compare MODEL --provider PROVIDER --provider PROVIDER
python3 scripts/query_model_prices.py compare MODEL --exact
python3 scripts/query_model_prices.py compare MODEL --include-overseas

# One hosting channel.
python3 scripts/query_model_prices.py provider PROVIDER MODEL --format message
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX

# A catalogue scan, with an artifact for later offline editing.
python3 scripts/query_model_prices.py delta --format message --save-payload RUN/scan.json

# Render that same run; no network, self-update, cache or baseline writes.
python3 scripts/query_model_prices.py render RUN/scan.json
python3 scripts/query_model_prices.py render RUN/scan.json --introductions RUN/introductions.json
python3 scripts/query_model_prices.py render RUN/scan.json --notes
```

`--notes` prints the delivery note instead of the message: the channels the
message counted but did not name, their reasons, source coverage gaps, and
per-channel baselines. It is the same saved evidence, so nothing has to be
re-derived for the note.

Use `--format message` for a person or any IM delivery, and `--format json`
for machine consumption. `delta` defaults to message; named-model queries
default to JSON. `--save-payload` saves the full scan JSON alongside either
output format and refuses an existing path. Use a unique run directory under
`cache/reports/` or outside the repository. Never rerun a scan to get another
format or a shorter report.

Provider IDs:
`aliyun`, `volcengine`, `tencent`, `baidu`, `deepseek`, `kimi`,
`zhipu`, `minimax`, `xiaomi`, `kling`, `ant-ling`, `openai`,
`anthropic`, `google`, `xai`, `mistral`, `openrouter`, `google-cloud`,
`aws-bedrock`, `azure`.

Default scope is domestic. A named OpenAI/GPT, Anthropic/Claude,
Google/Gemini, xAI/Grok, or Mistral family adds its first-party provider for
`compare` only. Names never imply an aggregator or reseller. Select those
with `--provider`; `--include-overseas` includes all 20 channels and is
appropriate when the user asks for all channels. AWS reads large public price
lists, so avoid an unsolicited whole-catalogue scan for a named-model question.

Use family matching unless the user needs a literal ID (`--exact`). Report the
actual returned ID; documentation footnotes and vendor spellings may normalize,
but never merge generations, hosting modes, regions, currencies, or billing tiers.

## Freshness and baselines

Lists and queries reuse a 3-hour cache. `--refresh` bypasses it for selected
providers. `delta` always reads fresh sources, once per document in the run,
and archives each successful provider result. Failures and empty scans do not
replace successful history. A listed model without a readable rate remains
listed with an unknown price; a published zero remains free.

Baseline choices are per provider:

- Omit `--since`: previous successful scan.
- `--since yesterday`: yesterday's last successful scan.
- `--since yesterday-first`: yesterday's first successful scan.
- `--since last-month`: previous calendar month's last successful scan.
- `--since YYYY-MM-DD`: that date's last scan.
- `--since ISO_TIMESTAMP`: last scan at or before that instant.

Use `--timezone Asia/Shanghai` for Beijing calendar days and scan timestamps.
A timestamp without an offset uses the scan timezone. Missing history is
`baseline_not_found`, never a substitute baseline or “no change”.

Explicit refreshes normally try a Git fast-forward and continue with a recorded
`skill_update` if updating fails; `--no-update` skips that check for a run that
must not touch the checkout. Daily delivery uses `delta --require-current`
instead: it requires a clean installed checkout at the fetched upstream commit,
verifies HEAD, and stops before any source read on failure. Update diagnostics
stay in the saved JSON/session, outside the scan message.

## Evidence boundaries

- Introductions belong to the **hosting channel and literal model ID**.
  Never borrow a creator's or another host's prose, even for the same model.
  Missing (`not_found`) and failed (`source_error`) introductions remain
  explicit and never suppress a valid price. Tencent's authenticated mirror is
  the only TokenHub detail source; an absent entry is not permission to infer.
- Publication, catalogue listing, developer access, and consumer access are
  independent. Keep published names without guessing API IDs. First observations
  are not claims that an old release launched today. Retained official releases
  survive rolling-index omissions; failures preserve their history.
- Catalogue disappearance proves only that catalogue's withdrawal. It does not
  establish API shutdown. Keep notice scope, EOM, redirects, EOS, earliest-possible
  qualifiers, replacements, date precision, and official URLs separately.
- Preserve the vendor's billing units, currencies, regions, delivery modes,
  context/time bands, promotions, and quoted terms. An official update time needs
  official evidence; a scan time is not a vendor update.
- OpenRouter's adapter excludes scan price and billing-mode movements from the
  comparison. Never reintroduce its amounts, price batches, or price-change counts
  during summarization. Its listing/notices/introductions remain visible;
  named-model queries still show its rates.
- Prefer the vendor's machine-readable Markdown or public JSON over rendered
  HTML. Do not request or store credentials. The sole console exception is an
  explicitly requested offline refresh of Tencent's model-description mirror.

## Delivery and maintenance

Read only the selected IM transport contract before sending. DingTalk's sole
compatibility and delivery contract is [im/dingtalk.md](im/dingtalk.md); do not
guess an equivalent command. The staged daily state machine lives in
[daily-workflow.md](references/daily-workflow.md), with personal recipients kept
in the user's request rather than this repository.

For JSON fields, read [references/schema.md](references/schema.md).
For source/parser repairs, read [AGENTS.md](AGENTS.md) and
[references/source-notes.md](references/source-notes.md).
For official publication coverage limits, read
[references/announcement-audit.md](references/announcement-audit.md).

Before publishing samples or diagnostics, follow
[references/privacy.md](references/privacy.md). Personal delivery details and real
installation paths stay in local run artifacts, outside the public repository.
