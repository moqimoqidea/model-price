# JSON output

Prices are strings. A comparison contains `query`, `match_mode`, `retrieved_at`,
`model_descriptions`, `results`, and `source_checks`.

`model_descriptions` contains one entry per canonical model, not one per hosting
provider. Each entry contains:

- `model_id`, `display_name`, and `canonical_model_id` when a match was found
- `observed_model_ids[]`: the provider spellings grouped into that canonical model;
  it is present for available and unavailable introductions
- `status`: `available`, `not_found`, or `source_error`
- `summary`, `capabilities[]`, `lifecycle`, and `specifications`
- for an available introduction, `source.name`, `source.url`, `source.kind`, and
  `source.retrieved_at`
- for an unavailable introduction, `note` and `attempted_sources[]`

`lifecycle` is `active`, `preview`, `legacy`, `retired`, or `unknown`. A missing
introduction never changes price-source status and never removes a price result.
When no price record matches the query, `model_descriptions` still contains one
honest `not_found` or `source_error` entry for the requested name.

Each result contains:

- `provider`, `model_id`, `display_name`, `model_family`
- `delivery_mode`, `region`, `currency`
- `offers[]`, each with an independent `name`, `conditions`, and `prices[]`
- `source.url`, `source.kind`, and `source.retrieved_at`
- optional `source_updated_at`, when the official price page or recent official
  release page publishes a date for that catalogue

Price fields are `type`, `label`, `amount`, `unit`, and optional `display`, `list_amount`, or `discount`. Common units are `CNY_per_million_tokens`, `USD_per_million_tokens`, their `_per_hour` storage variants, `CNY_per_10k_characters`, and `CNY_per_request`.

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
- `empty_scan` — the source yielded no priced model; all existing archives are kept
- `source_error` — the source failed; `error` says why and all existing archives are kept

The two failure statuses omit `model_count` and `changes`. `baseline_at` is `null`
when no matching baseline existed. A failed scan writes no archive.

`source.updated_at` is official vendor evidence copied into the snapshot; it is
`null` when the source publishes none. The plain-text report then displays
`last_successful_at` as 上次更新时间, rather than presenting a scan timestamp as an
official vendor update. Date-only official values remain date-only.

A `changed` provider also carries `model_descriptions`, covering each unique model
named anywhere in its changes. Unchanged and first-baseline providers omit it, so a
daily scan never walks every model detail page merely to repeat unchanged prose.

`changes` holds `models_added`, `models_removed`, `offers_added`, `offers_removed`,
`price_changes`, and their `total`. A model entry is the snapshot model, offers and
prices included, so a new model's price is readable without a second query. An
offer entry is a model plus `offer` (`name` and `conditions`). A price change is a
model plus `offer`, `conditions`, `type`, `label`, and `from`/`to` — each one an
`{amount, unit}` pair, with `null` on the side where the price did not exist.

Baselines are timestamped files under `snapshots/<provider>/`. Every successful
scan is archived, including an unchanged catalogue. Retention is hard-capped at
1000 snapshots per provider: reserve the last scan of each day in the recent
three-month calendar window, then fill remaining slots with the newest scans. A
legacy `snapshots/<provider>.json` remains readable and is migrated on the next
successful write; an unreadable one moves to `snapshots/rejected/`. Baselines are
not cache entries and not this payload:
a baseline keeps the catalogue keyed by normalized model id, with each offer
identified by its name and the conditions that describe what is billed rather
than where the document filed it.
