# Report preparation

Read when preparing a model-price answer for a person. The script preserves
facts; the host agent writes concise prose. No model or credentials are built
into the Python tool.

## Content contract

Messages are plain text: numbering, indentation, and blank lines; no Markdown
headings, emphasis, tables, or Markdown links. Put each full official HTTPS URL
last on its line. Never shorten or truncate a model ID, amount, condition, or URL.

Named-model comparisons open with each hosting channel's model introduction,
before conclusions and prices. Keep all returned versions, offers, sources, and
source statuses. Units, regions, service modes, context tiers, time windows,
currencies, discounts, expiry terms, and promotional wording belong to their own
price; never merge them into an average or a guessed common price.

Scan order is fixed: 模型价格自动检测 with scan/baseline context, 模型能力,
模型价格, 渠道结论. Empty sections disappear. Do not add a channel overview,
source inventory, separate retirement section, or Skill update section.

### Model capabilities

Group by hosting channel, then literal model ID. Preserve all simultaneous
causes: listing, withdrawal, price adjustment, billing-mode addition/removal,
replacement revision, notice/date/status/detail changes, crossed milestones,
official publication, access change, announcement-rate change, and publication
catalogue-state change. “Still listed” is not “newly listed”.

Use only that channel's official introduction and source. For Chinese reports,
write a short Chinese sentence explaining purpose and distinguishing capabilities
and specifications, rather than pasting an English product paragraph followed by
the same facts as capabilities/specifications. Preserve exceptional lifecycle
states; an active state is implicit. Missing or failed introductions stay explicit
without inventing an introduction or changing a price result.

The renderer writes identical display names/IDs once. Identical unavailable
introduction status, reason, and source are grouped once per channel with the full
affected ID list. Different errors or sources remain separate. Each model still
keeps every change cause and notice fact; grouping absence never groups away EOS,
replacement, access, or billing evidence.

Keep notice facts and evidence beside the affected channel's model. Preserve
scope, dates and precision, before/after replacements, and earliest-possible
qualifiers. A catalogue removal is not EOS; a planned or earliest-possible
shutdown is not proof that service has stopped. Publication and access statements
also keep their original audience and official evidence.

### Prices

The default scan selects standard catalogue offers for listed models. New models
without rates show 价格未知. Fast, flex, batch, and other nonstandard amounts
stay in JSON/snapshots; their billing-mode additions/removals still name modes
and conditions. Announcement rates are separate quoted historical evidence with
their full original terms, never today's catalogue bill.

When a channel has standard price movements on at least two distinct listed
models, both capabilities and prices use one 【价格调整】 item: every literal ID,
model count, standard price-change count, and the channel's full official HTTPS
catalogue URL. Never expand a linked batch into individual introductions or
amounts. Price-only models need no separate introduction in that batch; other
simultaneous causes remain separate entries. A single model's many amounts or
context tiers is not a batch. New listings, single-model adjustments, and
standard mode additions/removals keep detailed prices. Without a usable official
HTTPS page, keep details instead of making an untraceable batch.

This rule applies only to channels that report price movements. OpenRouter's
adapter excludes them: no scan amounts, price batches, billing-mode movements,
or price-change counts may be reconstructed downstream. Its catalogue amounts
remain available for named-model queries and archival use.

### Channel conclusions

Keep channel/model totals, all channel outcomes and change counts, and actual
baseline timestamps. Group unchanged channel names, but name every channel.
Distinguish price catalogue, retirement notices, and publication reads; a failed
independent read is a partial failure. Notice-only or publication-only changes
still count as changes. Preserve source_error, empty_scan, baseline_created,
baseline_not_found, and reasons; never label incomplete coverage “all unchanged”.

## Summarize before delivery

Prepare a concise final report even when the raw rendering fits. Summarize and
translate introductions, remove repeated explanatory wording, and keep the
selected structure. Do not ask whether the user wants another compression pass
or fewer channels. Channel scope is their choice, not a length-saving device.

There are two existing limits: introduction summaries at most 300 characters;
messages at most --max-chars (default 3000). The Python tool keeps overlong
originals intact and marks the overage; it never truncates or drops selected
facts. The host agent performs semantic summarization. Keep:

- Every selected model ID, change cause, exceptional lifecycle/access fact,
  milestone qualifier, replacement, and official source URL.
- Every amount the selected message displays, including before/after values,
  currency, billing unit, scope, time/context/region conditions, and price terms.
- Linked price batches in their original form, with all IDs and counts.
- Every channel state, failure reason, coverage gap, and actual baseline context.

Full introductions, capabilities, specifications, and prices remain in saved
JSON. Summarizing prose does not authorize weakening a fact, importing another
channel's claims, or omitting a failed source. Keep quotations used as access or
price evidence intact; summarize surrounding explanation instead.

Iterate on the saved artifact until the final text fits and is verified. Remove
the tool's overage instruction only when it no longer applies. If protected facts
alone cannot fit, explain that concrete constraint and resolve the delivery
choice; do not silently omit facts or send over the transport ceiling.

## Offline introduction editing

Use delta --save-payload RUN/scan.json during the single scan, then render that
same JSON as often as needed. Create RUN/introductions.json as a JSON list:

```json
[
  {
    "provider": "openrouter",
    "model_id": "vendor/exact-model-id",
    "summary": "根据该渠道原始介绍写出的中文用途、关键能力与规格。"
  }
]
```

Use exact provider IDs and literal model IDs from the artifact. Each entry must
contain exactly these three fields. A summary is one plain-text paragraph of at
most 300 characters. It combines purpose, capabilities, and key specifications;
those repeated fields are replaced by 用途与能力 in the rendered message.
Lifecycle and the original source line are retained independently.

```bash
python3 scripts/query_model_prices.py render RUN/scan.json \
  --introductions RUN/introductions.json > RUN/message.txt
```

Unknown IDs/providers, unavailable introductions, duplicate entries, extra fields,
multiline or overlong summaries are rejected. The renderer makes no source or Git
requests and writes no cache, snapshot, or source artifact. Without an override,
the original introduction remains whole. This validation checks scope and shape;
the agent must check fidelity against the saved official prose, capabilities,
specifications, and source before sending. The mechanism does not judge the truth
of a summary. See [schema.md](schema.md) for artifact fields.
