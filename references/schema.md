# JSON output

Prices are strings. A comparison contains `query`, `match_mode`, `retrieved_at`,
`model_descriptions`, `results`, and `source_checks`.

`model_descriptions` contains one entry per hosting provider and normalized
literal model ID. Identical IDs on different channels keep separate descriptions,
and retired-name aliases are not merged. Each entry contains:

- `provider.id`, `provider.name`, `model_id`, and `display_name`; `model_id` keeps
  the hosting channel's literal spelling rather than a canonical first-party ID
- `observed_model_ids[]`: spellings of that literal ID within the same provider;
  it is present for available and unavailable introductions
- `status`: `available`, `not_found`, or `source_error`
- `summary`, `capabilities[]`, `lifecycle`, and `specifications`
- for an available introduction, `source.name`, `source.url`, `source.kind`, and
  `source.retrieved_at`
- for an unavailable introduction, `note` and `attempted_sources[]`
- optional `reference_url` for an unavailable introduction: the same channel's
  official page for follow-up, not evidence that capabilities were read there

`lifecycle` is `active`, `preview`, `legacy`, `retired`, or `unknown`. A missing
introduction never changes price-source status and never removes a price result.
The resolver reads only that provider's registered description source. A missing
or failed introduction never falls back to another channel or the model creator.
When no price record matches the query, `model_descriptions` still contains an
honest status entry for the requested name on each queried channel.

Each result contains:

- `provider`, `model_id`, `display_name`, `model_family`
- `delivery_mode`, `region`, `currency`
- `offers[]`, each with an independent `name`, `conditions`, and `prices[]`
- `source.url`, `source.kind`, and `source.retrieved_at`
- optional `source_updated_at`, when the official price page or the official update
  log publishes a date for that catalogue
- optional `pricing_state`, when the vendor lists the model without any rate to bill
  (`free` — published as zero, whether as a free variant or while under test — or
  `varies`, a router priced by whatever it routes to). The model then has no
  `offers` at all, and the state is what the price line states instead of a price.
  A vendor that actually charges nothing publishes a price of `"0"` instead; the
  two are different facts and are never written the same way

Price fields are `type`, `label`, `amount`, `unit`, and optional `display`,
`list_amount`, `discount`, or `effective_until`. Endpoint-attributed prices may
also carry `provider_name`, `provider_tag`, and `list_amount_basis`.

Mistral adds the `thousand_pages` measure for its OCR rates and reads `/M Chars`
as `million_characters`. Its records retain each literal published API identifier
and alias separately; displayed model names and detail-page slugs do not create
IDs. An offer's `service_tier` retains its pricing mode, while `inference_scope`
is `default` or `regional`, distinguishing the vendor's regional surcharge
without inventing a geographic deployment. Both conditions participate in offer
identity. Billed and struck-through original amounts are scaled independently by
the factors the official controls publish. A listing absent from the price table
has empty `offers`, even if an old model detail still contains a rate.

`unit` is a code built as `<currency>_per_<measure>`, so a new currency or a new measure costs one entry in the vocabulary rather than one entry per combination. The measures read today are `million_tokens`, `million_tokens_per_hour` (cache storage), `thousand_tokens`, `10k_tokens`, `million_characters`, `10k_characters`, `thousand_characters`, `character`, `image`, `frame`, `second`, `minute`, `hour`, `request`, `thousand_requests`, `10k_requests`, `video`, `item`, `song`, `page`,
`million_video_tokens` (a vendor that bills video by the token, which is not a
language token rate), `hundred_images` (an amount published for a hundred
pictures, kept at the scale the page printed it), and `megapixel_second` (a video
upscaler, billed by the area it processes over the time it runs) — `CNY_per_image` is 元/张, `USD_per_second` is
美元/秒, `CNY_per_million_tokens_per_hour` is cache storage. A vendor that bills in its own credit keeps that credit as the unit (`积分/次`): the amount is a price, and converting it would print a rate the page never published. A price whose vendor stated no unit at all carries `provider_defined`, which is rendered as the amount alone.

Two things a vendor publishes beside an amount decide which of several prices one cell holds. A cell that prices several tiers states each tier's scope in the cell itself, and that scope is `conditions.price_scope` on the offer the amount belongs to — one offer per scope, because merging them would quote one tier's rate for another. A cell that lists several model IDs prices each of them, and each becomes a model of its own rather than a note on the first.

`amount` is always what a purchase is billed at today. The three optional fields beside it are the terms the vendor published with it, and each is shown with the amount rather than instead of it:

- `list_amount` — the other rate the vendor printed for the same charge, written as `（原价 …）`. A promotion publishes both numbers, so the lower one is never taken for what the model ordinarily costs.
- `discount` — the multiplier the vendor itself published, written as the 折 a Chinese page reads (`0.5` is `5 折`, not `0.5 折`). It is recorded only where the vendor states one; where a vendor instead prints two dated amounts, the pair is recorded and no ratio is derived from it.
- `effective_until` — the last day `amount` applies, written against the amount (`0.75 美元/百万 tokens 至 2026-12-31（原价 1.50）`) so the date cannot be read as qualifying the rate beside it. Where a vendor dates its rate per row, the date sits on the price; where it dates a whole activity in a banner, the window sits on the offer as `promotion_window` below.

OpenRouter endpoint evidence uses the same paid-multiplier convention: the API's
fractional reduction of `0.55` becomes `discount: "0.45"`, and its own
pre-discount amount is calculated from the official formula. Such a price carries
`list_amount_basis: "endpoint_discount"` and reads as
`端点未折价（按折扣还原）`, not an independently published original rate.
`discount: "1"` means the endpoint explicitly published no reduction;
`discount: "0"` is a full reduction, for which no pre-discount price is inferred.
The raw API reduction is retained separately. Missing or invalid discounts leave
`list_amount` unknown. These fields describe that hosting endpoint, never a
different provider or the model creator.

A vendor that runs an activity prices it as an offer of its own: `name` is the activity's own wording, and its `conditions` add `channel` (the serving channel it prices, which the activity's name has taken over from the standing offer) and `promotion_window` (the window the vendor published, in the vendor's words). Those two stay out of the offer's `name` because they are terms, and both are part of the offer's identity: an activity that ends is an offer that ended, not a price that moved. A standing rate carries neither.

A charge the vendor publishes as free is a price of `"0"` with `display` carrying the vendor's own wording (`免费`, `限时免费`, `Free of charge`), never a missing `amount`. `amount` is `null` only when the source priced nothing this tool can read — a tier the page does not quote, or a figure published without the currency it is in. The two are different facts and are never made to look alike.

A vendor that explains a price in prose rather than in a column gives the record a `pricing_notes` list holding its own sentences, verbatim:

```json
{
  "model_id": "gpt-5.6-sol",
  "pricing_notes": [
    "GPT-5.6 Sol’s promotional pricing is available at least through November 21, 2026."
  ]
}
```

The sentence is kept as written because it says something a field cannot: *at least through* a date is not an end date, and 达到用量上限后恢复按刊例价结算 is not a multiplier. It never replaces the price — that is still read from the table and is the rate billed today — and it is never compared, so a note that is reworded is not a change. Only sentences naming that model are kept, and a model no sentence names carries no key at all.

`delivery_mode` is `self_deployed`, `platform_hosted`, `third_party_hosted`, `upstream_direct`, or `first_party`. Source status is `available`, `not_found`, or `source_error`; the last means availability and price are unknown.

When a vendor bills by time of day the result also carries `time_bands`:

```json
{
  "time_bands": {
    "window": "周一至周五 09:00–12:00、14:00–18:00；其余均为空闲时段",
    "statements": ["the vendor's own sentence, quoted verbatim"],
    "source_url": "https://…"
  }
}
```

`window` is the compact form repeated on every peak/off-peak row, `statements` are the vendor's own sentences, and an empty object (`{}`) means the vendor publishes no window. The window is read from that vendor's own document and is never inferred from another vendor's schedule.

Cached operations add:

```json
{"cache": {"status": "hit|miss|refreshed|memory_hit|refresh_failed", "fetched_at": "ISO-8601"}}
```

Explicit refreshes also add `skill_update` before querying official sources:

```json
{
  "skill_update": {
    "status": "updated|up_to_date|update_skipped|check_failed",
    "checked_at": "ISO-8601",
    "upstream": "origin/main",
    "from_revision": "git commit",
    "to_revision": "git commit",
    "reason": "present for skipped or failed checks"
  }
}
```

## Change reports

`delta` reports `command`, `retrieved_at`, `baseline_selection`, `summary`, and `providers`, with
`skill_update` added on the same terms as a query:

```json
{
  "command": "delta",
  "retrieved_at": "ISO-8601",
  "baseline_selection": {
    "mode": "latest|yesterday|yesterday_first|last_month|date|at_or_before",
    "requested": "original --since value, or null",
    "target": "normalized ISO date or timestamp, or null",
    "uses_scan_timezone": false
  },
  "summary": {
    "providers": 9,
    "changed": 1, "unchanged": 7, "baseline_created": 1,
    "baseline_not_found": 0, "empty_scan": 0, "source_error": 0,
    "models_added": 2, "models_removed": 1,
    "offers_added": 1, "offers_removed": 0, "price_changes": 3
  },
  "providers": []
}
```

### Official model announcements

The CLI's `delta` also adds independent `announcements` evidence to every selected
provider. `compare`, `provider`, and `list` continue to discover from catalogues.
Price report `status` and `changes` retain their original meanings; announcement
discoveries never become fake `models_added` price records. The announcement
result contains:

- `status`: `changed`, `unchanged`, `baseline_created`, `baseline_not_found`,
  `no_announcements`, `source_error`, or `catalogue_only`
- `source.url/kind`, `coverage`, and `note`: the registered source and its scope,
  retained even on read failures. `coverage` is `recent_official_news`,
  `official_model_inventory`, or `catalogue_only` (with `source: null`)
- `baseline_at`, `last_successful_at`: independently selected and latest successful
  announcement archive timestamps
- `models[]`, optional `model_count`: all retained publication records, not only
  the current rolling index; a record omitted from that index is not retracted
- `changes[]`, `observations[]`, and `error` on a failure

Each publication record carries:

- `model_id`, `display_name`: the literal official published name, or the literal
  ID its capability document publishes; no generated API slug
- `identity_kind`: `published_name` or `document_model_id`
- `source_url`, `source_name`, `published_at`, and `summary`: official evidence,
  preserving date precision and complete prose. A missing official date is `null`
- `access.status/developers/consumers`: each is `unknown`, `pending`, `limited`, or
  `public`, established by the official wording; `access.statements[]` retains
  that wording and any different access scope within it
- `announced_offers[]`: separate quoted announcement rates, each with `name`,
  `conditions.published_terms`, and ordinary price-shaped `prices[]`; introductory
  and later prices remain separate offers. These never join live `offers[]`
- `pricing_notes[]`: verbatim pricing sentences, including percentages and
  imprecise promotion periods; no derived prices, discounts, or expiry dates
- `catalog_model_ids[]`: exact normalized literal ID/display-name matches on this
  provider only, with no family/retirement aliases and no inferred variants
- `catalog_status`: `listed` when an exact match exists, `not_listed` when no
  same-name entry matches a successfully read catalogue, `unknown` when the
  catalogue could not be verified. A `not_listed` match result is not evidence of
  closed service, and an ambiguous set of matches never merges several IDs
- optional `description`: an already verified same-channel inventory introduction
- optional `detail_status: source_error`, `detail_error`, and `article_url` when
  only the official index was readable. Then `source_url` identifies that index,
  and unavailable article prose never becomes a capability claim

`changes[].kind` is `announcement_observed` on the first default scan, then
`model_announced`, `access_changed`, `announced_price_changed`, or
`announcement_listing_changed`. Every change keeps its full `event`; revisions
also carry `field`, `before`, and `after`. Access revisions compare status enums;
announced-rate revisions compare each offer's `(type, amount, unit)` rates, so
reworded evidence alone is not a rate movement. `unknown` catalogue reads do not
pretend the model was listed or removed.

An initial `baseline_created` announcement result carries first observations,
unlike the initial price baseline's empty changes: these prove prior official
publication, not a launch on the scan date. A historical selection with no
announcement archive reports `baseline_not_found` with no invented delta, while
archiving the current successful read. New records absent from the most recent
archive remain visible under `observations[]` as `announcement_observed`; these
are explicitly first observations, not `--since` changes, and are not repeated
on the next read. A readable news index with no qualifying
model and no retained history gives `no_announcements` and writes no empty
baseline. Complete or partial failures give `source_error`, keep the prior
archive, and can still display verified index discoveries. Each independent
source's failures leave the other successful histories untouched.

Announcement archives live under `snapshots/announcements-<provider>/`, use
`announcement_schema_version: 1`, and retain `captured_at`, `provider`, `source`,
and `models` keyed by the normalized published identity. They reuse the same
point-in-time selection and retention policy without changing price snapshot
version 4 or lifecycle version 2. Model summaries continue to use the shared
300-character condensing flag; full announcement data is never cut for a budget.

`summary.announcement_changes` counts all publication/access/rate/listing causes;
`summary.announcement_observations` counts first observations when the selected
historical announcement baseline is absent, separately from comparison changes;
`summary.announcement_source_errors` counts independent incomplete reads. The
message combines these with catalogue/retirement outcomes. Capabilities appear
first, including explicit access evidence; 公告报价 is separate from live directory
prices in 模型价格. All quoted amounts and terms remain visible even over budget.

### Retirement notices

When the CLI runs `delta`, each provider also carries an independent `lifecycle`
result. Its `status` is `changed`, `unchanged`, `baseline_created`,
`baseline_not_found`, `source_error`, or `no_public_schedule` (for an unregistered
provider). A price-source
failure does not suppress a readable retirement notice, and a notice-source
failure does not erase a successful price comparison. `source` names the official
notice/index, `baseline_at` names the selected retirement baseline,
`last_successful_at` names its most recent successful read, and `event_count`
counts retained model records. `notice_model_ids[]` lists literal IDs appearing
in retained official notices, including future and legacy entries.
`retired_model_ids[]` lists literal IDs with
confirmed retirement as of this scan: an explicit “already retired” status, a
due definite EOS, or a due automatic redirect. It is retained from the last
successful notice read when the current read fails. `changes[]` contains:

- `kind`: `new_notice`, `date_revised`, `detail_revised`, or `milestone_reached`
- `event`: the vendor's literal `model_id`, `scope`, `source_url`, optional
  `announced_at`, `eom_at`, `redirect_at`, `eos_at`, `replacement`,
  `end_behavior`, `eos_earliest`, and `notice_status`
- for `date_revised`, `milestone`, `before`, and `after`; for
  `milestone_reached`, `milestone`; for `detail_revised`, `field`, `before`,
  and `after`

Dates remain at the precision the vendor published. `eos_earliest` means the
vendor gave the earliest possible shutdown date, so crossing it is not evidence
that service actually stopped. `end_behavior` is `redirect`, `unavailable`,
`existing_access_continues`, or `unknown`. Model IDs are scoped to the hosting
provider: the same model name on two platforms can have different dates.
Tencent's conditional automatic migrations retain their qualification in `scope`.
A target described as the latest version at the future migration time keeps
`replacement: null`; the model that happened to be latest on announcement day
does not establish that target. Separately published redirect and EOS clocks
retain their independent dates and seconds.
Ant Ling's `计划下架日期` and `下架日期` are definite EOS clocks in the published
UTC+8 zone. Its `已下架` section additionally supplies `notice_status: retired`;
future notices use `scheduled`. A recommended replacement keeps its literal ID,
with `end_behavior: unavailable` and no `redirect_at`: the official policy says
old-ID API calls fail and asks developers to migrate manually. The current-model
table supplies no retirement events. Unreadable notice rows preserve the prior
notice archive independently of a successful price scan.
`notice_status` is `scheduled`, `legacy`, `retired`, or null. `legacy` identifies
an old-version bucket without claiming shutdown; `scheduled` needs a due definite
date before it counts as retired; `retired` is an explicit already-down notice,
including Google's gray row marker even when the row has no date.
`summary.lifecycle_changes` and `summary.lifecycle_source_errors` count these
results separately from price changes when lifecycle scanning is enabled.

For changed models, a provider report carries `model_availability`, a mapping
from its literal model IDs to `listed`, `delisted`, `announced`, or `unknown`.
`announced` identifies official publication without a same-name current catalogue
match; `unknown` identifies announcement evidence whose catalogue read failed.
Neither establishes API availability, which is kept in `announcements.access`.
`delisted` means the model
left that provider's monitored price catalogue without a still-open official
notice, or has confirmed retirement evidence. A future or undated legacy notice
keeps a model listed even if that price page has no row for it. Catalogue removal
alone does not prove the API stopped serving the ID.
Availability controls which catalogue prices can appear in 模型价格; separately
quoted announcement rates retain their published scope. Availability never replaces a
change category. 模型能力 groups literal IDs by provider and labels every actual
cause: 新增上架, 目录下架, 价格调整, 计费模式新增/移除, 替代模型更新, 退役公告新增,
退役日期更新, 官方状态更新, 退役信息更新, or 退役时间节点. One model can carry
several labels, and its notice facts and evidence appear beside its own channel's
introduction. There is no separate retirement section. Catalogue removal does not
establish actual service shutdown.

If one channel has standard price movements on at least two distinct listed
models, 模型能力 and 模型价格 each show one linked batch item. It names every
literal model ID and the standard movement count, without individual amounts or
repeated price-only introductions. Models with additional changes retain those
separate facts. New-model prices, added/removed standard offers, and single-model
price movements remain detailed. JSON retains every description, price change,
amount, condition, and removed-model count; batching never changes this payload
or the stored baselines and does not depend on the character budget.

Aggregated batches retain this threshold and layout, but their tag/count read as
`目录价波动`/`目录价变化`. Provider, discount, and cause evidence is grouped into
the same item; the scope note distinguishes hosting rates from creator prices.

Notice archives are under `snapshots/lifecycle-<provider>/`, independent of the
price archives and with their own `lifecycle_schema_version`. A failed or empty
notice read writes no retirement baseline. Older notices absent from a shortened
index are retained as known evidence rather than treated as retractions. A
retirement schema bump starts a new comparison baseline while carrying prior
notice events forward so old rolling-index links remain traceable.

Each successful entry of `providers` carries `provider`, `status`, `baseline_at`,
`last_successful_at`, `captured_at`, `source`, `model_count`, and `changes`.
`baseline_at` is the archive actually selected for comparison;
`last_successful_at` is the latest successful scan before this run and remains the
fallback for 上次更新时间 even when `--since` selected an older archive. `status` is
one of:

- `changed` — `changes` holds what moved
- `unchanged` — `changes` is empty; the models and prices are the baseline's
- `baseline_created` — there was no baseline yet, so `changes` is empty and the run is not a comparison
- `baseline_not_found` — `--since` matched no archive for this provider; the current successful scan is still archived
- `empty_scan` — the source yielded no model; all existing archives are kept
- `source_error` — the source failed; `error` says why and all existing archives are kept

The two failure statuses omit `model_count` and `changes`. `baseline_at` is `null`
when no matching baseline existed. A failed scan writes no archive.

The scan adds `catalog_url` to every provider report: that adapter's official
human-readable catalogue when declared, otherwise its price source URL. It is
used for batch follow-up links and unavailable-introduction references. It is
runtime metadata and does not change the price or lifecycle snapshot shapes.

An adapter with `aggregated_pricing: true` also carries that policy in records,
price snapshots, and provider reports. It changes price-change wording to
`目录价波动`/`目录价变化`; reporting never infers the policy from a provider ID.
Other adapters default to false. The policy does not change the amount comparison
or the two-model batch threshold.

`summary.changed` and `summary.unchanged` remain catalogue comparison counts;
`summary.lifecycle_changes` counts notices separately, and
`summary.announcement_changes` counts publications and access separately. The message's combined
channel conclusion also counts notice-only changes as changed and partial notice
read failures as incomplete, naming the price and notice outcomes independently.

`source.updated_at` is official vendor evidence copied into the snapshot; it is
`null` when the source publishes none. `last_successful_at` retains the fallback
scan time without claiming an official update; the scan message prints no source
update times. Date-only official values remain date-only.

A provider with catalogue, notice, or announcement changes also carries `model_descriptions`,
covering each unique literal model named in those changes. This includes
notice-only changes on a provider whose price status is `unchanged` or
`source_error`. Providers with none of those changes omit it, so a daily scan
never walks every detail page merely to repeat unchanged prose.

`model_count` includes listed models with no parseable price. `changes` holds `models_added`, `models_removed`, `offers_added`, `offers_removed`,
`price_changes`, and their `total`. A model entry is the snapshot model, offers and
prices included, so a new model's price is readable without a second query. Each
model has `price_status` (`published` or `unknown`); `unknown` means the source
published no price for it at all, so `offers: []`, and it still appears in
`models_added`. A model the vendor publishes as free is `published`, because a zero
is a price. When an unpriced model's price is later published, the change
appears in `offers_added`, not `models_added` again. An
offer entry is a model plus `offer` (`name`, `conditions`, and `prices`). A price change is a
model plus `offer`, `conditions`, `type`, `label`, and `from`/`to` — each one the
billed `{amount, unit}` pair together with any price term that side carried, with
`null` on the side where the price did not exist. A change is decided by the amount
alone; the terms travel with it for the reader, so `6 → 4.8` never has to be read
without `5 折` beside it.

### Aggregated-price attribution

OpenRouter's `delta` enriches only models with price movements against the selected
or latest archive. A standing offer may carry `pricing_attribution` independently
of `conditions`; it never becomes part of `offer_identity`:

- `status`: `matched`, `ambiguous`, `not_found`, or `source_error`
- `source.url/kind`, `observed_at`, optional `error`: the endpoint document and
  observation/attempt time; unchanged scans retain the original time
- `endpoints[]`: each endpoint's literal `provider_name`, `tag`, optional `name`,
  `model_id`, `context_length`, `status`, `quantization`, raw `pricing`, and
  normalized `prices[]`. Endpoints stay separate; a model's prices are never the
  minimum of this array
- `selected_endpoint`: the uniquely available whole-vector price match, or null
- `reference_endpoint`: the first uniquely matched tagged endpoint, subsequently
  kept as a stable supplier reference. Missing/unavailable/failed reads retain the
  last reference, never substitute another host
- `reference_status`: `observed`, `not_found`, `unavailable`, or `source_error`
- `reference_observed_at`: when the reference itself was last observed; a retained
  endpoint is historical evidence, not proof of present availability or price

Each aggregated `price_changes[]` entry adds:

- `cause` and `causes[]`: `price_adjustment` (the same pinned endpoint's
  reconstructed pre-discount rate moved), `promotion_change` (observed discount
  change on the same endpoint), `provider_switch` (unique matched endpoint changed),
  or `catalog_price_fluctuation` (no verified primary adjustment, promotion, or
  provider switch). The primary cause
  gives a base-rate adjustment precedence, while the list preserves simultaneous
  observations. Different hosts' base prices are never compared as one list price
- `provider_name`, `discount`, `list_amount`, `endpoint_discount`: the selected
  endpoint and current normalized terms, or null where unavailable;
  `endpoint_discount` preserves the API's reduction fraction
- `pricing_attribution`: `status`, `source`, `error`, `observed_at`,
  `reference_provider_name`, `reference_tag`, `reference_status`,
  `reference_from`/`reference_to` (amount/unit plus normalized terms),
  `reference_baseline_at`, `reference_observed_at`, and `reference_unchanged`
  (whether that same reference's known pre-discount rate held still, otherwise null)

Only actual billed amount/unit movements enter `price_changes`; new evidence or
term changes alone never create a delta. Old snapshots remain compatible but
cannot establish historical promotions or routes they did not observe. Batch
messages keep all IDs and the official catalogue link in one item per section,
with neutral aggregated wording and attribution instead of expanded amounts.
Single-model movements keep their complete amounts and normalized terms.
Verified reference adjustments are explicitly marked within the same batch;
single-model rows show the reference's own pre-discount movement separately from
the catalogue movement. A reference comparison is relative to its dated endpoint
observations, not proof of a change on the selected catalogue baseline day.
Unchanged model-level prices do not trigger new endpoint observations.

An unexpected enrichment failure adds provider-level
`pricing_attribution: {"status": "source_error", "error": "…"}` while archiving
the valid unmodified catalogue. Known endpoint read failures are scoped to their
offers/movements instead. Neither failure fabricates a reference adjustment.

These fields are additive evidence, so snapshot version 4 is retained. The parsed
response cache version is bumped. No whole-catalogue endpoint enumeration, global
list-price comparison, historical backfill, or scan debounce is introduced.

Baselines are timestamped files under `snapshots/<provider>/`. Every successful
scan is archived, including an unchanged catalogue. Retention is hard-capped at
1000 snapshots per provider: reserve the last scan of each day in the recent
three-month calendar window, then fill remaining slots with the newest scans. A
legacy `snapshots/<provider>.json` remains readable and is migrated on the next
successful write; an unreadable one moves to `snapshots/rejected/`. Baselines are
not cache entries and not this payload:
a baseline keeps the catalogue keyed by normalized model id, with each offer
identified by its name and the conditions that describe what is billed rather
than where the document filed it. Snapshot schema version 2 retains unpriced
listings; older version 1 baselines are not compared with it.
