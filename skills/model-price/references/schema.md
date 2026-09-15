# JSON output

Prices are strings. A comparison contains `query`, `match_mode`, `retrieved_at`, `results`, and `source_checks`.

Each result contains:

- `provider`, `model_id`, `display_name`, `model_family`
- `delivery_mode`, `region`, `currency`
- `offers[]`, each with an independent `name`, `conditions`, and `prices[]`
- `source.url`, `source.kind`, and `source.retrieved_at`

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
{"cache": {"status": "hit|miss|refreshed|refresh_failed", "fetched_at": "ISO-8601"}}
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
