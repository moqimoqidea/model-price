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

When a model carries a retirement milestone (EOM, redirect, EOS, or official
retirement), do not print a launch-style introduction for it: a "now available"
claim beside "停止新购" reads as its opposite. Omit that introduction and its
source and keep the retirement evidence; an introduction that itself states the
retirement still belongs.

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

Only an `amount_changed` entry is a 价格变化. A `unit_changed` entry is a 单位修正;
a `line_added`/`line_removed` entry is a 计费项变化; a `caption_changed` entry has
no price consequence and stays in JSON. A mass catalogue removal whose models
share one official notice is one 【目录下架】 item naming every ID, replacement,
dates, and notice URL; a removal with another cause or different notice stays
detailed.

This rule applies only to channels that report price movements. OpenRouter's
adapter excludes them: no scan amounts, price batches, billing-mode movements,
or price-change counts may be reconstructed downstream. Its catalogue amounts
remain available for named-model queries and archival use.

### Channel conclusions

Keep channel/model totals, change counts, and the names of every channel that was
compared. Group unchanged channel names, but name every compared channel. A channel
without a usable comparison is counted in the summary line as "N 个本次未纳入" and
is not named: its reason, a failed independent read, a coverage gap, and its own
baseline belong to the delivery note, which `render --notes` prints from the same
saved payload. Never label incomplete coverage "all unchanged".

The message states one baseline fact, "对比基线：<时间>"; when channels compared
against different moments, it states the range instead. Per-channel baselines and
missing-history statuses stay in the delivery note. Notice-only or publication-only
changes still count as changes.

### Shorter delivery (digest)

A standard message is complete. When the host's delivery budget is stricter than
`--max-chars`, it may send a digest of the same saved comparison. A digest is a
presentation choice, never a different result:

- It carries no capability or introduction prose, so it makes no claim that
  needs a source line in the message. The saved artifact keeps every
  introduction and its source.
- Every displayed model ID, amount, unit, currency, region, condition, date, and
  replacement stays exact. Only enumeration length and explanatory prose may
  shrink; a string is never sliced.
- A retirement or removal enumeration may name one or two literal IDs plus the
  total and mark itself as representative ("15 项，如 …"). Every ID remains in the
  saved artifact, and the delivery note accounts for what was represented.
- Prices keep the standard tier and the tiers the change touches. Equal-change
  tiers merge into one line; the rest are named as "其余 N 档见目录".
- Excluded channels stay counted, never named in the body; their names, reasons,
  and baselines belong to the delivery note.
- The delivery note records every represented or omitted fact and the final
  character count.

A digest is never a silent omission: it marks a representative list as such, and
its delivery note names what was represented or left out.

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
- The amounts the final message displays, including before/after values, currency,
  billing unit, scope, time/context/region conditions, and price terms. When space
  is short, keep the main price — the standard tier plus the tiers the change
  touches — and merge equal-change tiers into one, naming the rest as "其余 N 档见
  目录". A displayed amount is never truncated, and a dropped tier must not be the
  one that carries the change.
- Linked price batches in their original form, with all IDs and counts.
- Every compared channel's state and change count. Excluded channels stay counted
  in the body and are explained in the delivery note.

A digest sent under a stricter delivery budget follows the digest rules above: it
may name one or two representative IDs plus the total instead of an entire
enumeration, and its body carries no introduction source line; the delivery note
then accounts for every represented or omitted fact.

Full introductions, capabilities, specifications, and prices remain in saved
JSON. Summarizing prose does not authorize weakening a fact, importing another
channel's claims, or omitting a failed source. Keep quotations used as access or
price evidence intact; summarize surrounding explanation instead.

Iterate on the saved artifact until the final text fits and is verified. Remove
the tool's overage instruction only when it no longer applies. If protected facts
alone cannot fit, use the digest rules above rather than silently omitting facts
or sending over the transport ceiling.

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
