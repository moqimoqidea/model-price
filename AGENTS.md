# AGENTS.md

Guidance for an agent **changing this repository**: how the code is layered, which
invariants must survive a refactor, and where a given change belongs.

This file deliberately does not restate the other documents.

| Document | Read it when |
| --- | --- |
| `README.md` | you need what the skill does, how to install it, or why the report is shaped the way it is (for a person) |
| `SKILL.md` | you are **using** the skill at runtime and need to pick a command, a provider set, and a format |
| `references/schema.md` | you are consuming or changing the JSON payload |
| `references/source-notes.md` | you are touching one vendor's source or parser |
| `AGENTS.md` | you are **maintaining** the code — this file |

## What this is

A single-skill repository whose root **is** the skill directory. It queries public,
credential-free model catalogues, introductions, capabilities, specifications, and
official price documents across 13 providers. Its two output modes are a model and
cross-provider comparison, and a whole-catalogue scan that reports launches,
withdrawals, billing changes, and price moves since the previous scan.

Standard library only, Python 3, no install step, no build step.

## Layout

```
.
├── SKILL.md                     runtime contract the skill host loads
├── README.md                    the person-facing document
├── AGENTS.md                    code structure, invariants, and change locations
├── agents/openai.yaml           platform interface metadata (display name, default prompt, policy)
├── references/
│   ├── schema.md                the JSON payload, field by field
│   └── source-notes.md          per-vendor source and parser notes
├── scripts/
│   ├── query_model_prices.py    the stable CLI entry point — argument parsing and dispatch only
│   ├── update_tencent_model_mirror.py   offline maintenance entry point
│   └── model_price/             the implementation
│       ├── __init__.py          the responsibility split, restated for a reader
│       ├── paths.py             filesystem anchors, cache TTL, schema versions
│       ├── errors.py            SourceError, SkillUpdateError
│       ├── core.py              HttpClient, the PriceSource contract, atomic JSON writes
│       ├── text.py              normalisation of text scraped out of vendor documents
│       ├── models.py            model identity: normalisation, aliases, family matching
│       ├── pricing.py           price and record shapes, unit rescaling
│       ├── parsing.py           document readers: HTML/Markdown tables, price headers, time bands
│       ├── caching.py           CacheStore and the CachedPriceSource decorator
│       ├── updating.py          Git fast-forward before an explicit refresh
│       ├── snapshots.py         the per-provider baseline a later scan is compared against
│       ├── diffing.py           what moved between two baselines
│       ├── delta.py             the scan-every-catalogue run
│       ├── reporting.py         the shared wording: one value, written once
│       ├── messages.py          the two plain-text messages those values are laid into
│       ├── budget.py            how much of a message fits the channel carrying it
│       ├── registry.py          wiring, provider selection, cross-provider queries
│       ├── providers/           one module per vendor, registered in its __init__.py
│       └── descriptions/        the independent model-introduction subsystem
│           ├── __init__.py      the subsystem's public surface
│           ├── core.py          the DescriptionSource contract and record shape
│           ├── sources.py       one class per official introduction source
│           ├── parsing.py       Markdown/HTML readers specific to introduction pages
│           ├── resolver.py      pick one authoritative introduction per canonical model
│           ├── registry.py      construct the resolver
│           ├── tencent_mirror.py  validate the checked-in Tencent capture
│           └── data/tencent-models.json   the checked-in mirror
├── tests/                       unittest, no network
├── cache/                       runtime state, contents not in git
└── snapshots/                   runtime state, contents not in git
```

## How a run flows

A query and a scan share the front half and diverge at the adapters.

```
query_model_prices.py                        CLI: parse args, select providers, render
  └── registry.build_adapters()              one CachedPriceSource per provider
        └── CachedPriceSource( ProviderAdapter( HttpClient ) )

compare / provider / list
  ├── registry.select_compare_providers()    requested > inferred overseas > domestic
  ├── adapter.search(model)
  │     ├── list_models() → model_matches()
  │     └── adapter.query() → parse the document → pricing.make_record()
  ├── descriptions.resolve_many(every model)
  └── messages.comparison_message(payload)

delta
  ├── registry.select_catalog_providers()    never infers an overseas provider
  ├── adapter.catalog_records()              always fresh — a cache hit reads as "no change"
  ├── snapshots.build_snapshot() → SnapshotStore.write()
  ├── diffing.compare_snapshots(previous, current)
  ├── descriptions.resolve_many(changed models only)
  └── messages.scan_message(payload)
```

The output layer reads the domain layer; the domain layer never reads the output
layer. `messages` formats nothing itself — it asks `reporting` for each value and
its two entry points are the only things that know how a message is laid out:

- `reporting.py` — one value, one wording: labels for every enum a source can
  publish, an amount in the unit it was billed in, a moment with its offset.
- `messages.py` — the layout: headings, numbering, indentation, blank lines, and
  the block list each entry point renders.
- `budget.py` — how far a message overruns its limit, measured and nothing else:
  it counts characters and never touches a line of text.

Never move a value formatter into `messages.py`, never let `reporting.py` decide
where a line goes, and never let `budget.py` know what a message says.

### How a message stays within its limit

Every block a report can carry is always rendered. Two limits exist and both are
handled the same way — by measuring and saying so, never by cutting:

- **The introduction limit** (300 characters) lives at `descriptions.core`: a
  vendor summary longer than that is kept whole and marked
  `summary_needs_condensing`. `reporting.summary_label` turns that flag into a
  label naming the original length, so the reader sees that this one has to be
  summarized before it is sent. The flag rides on the record, which is what gets
  cached, so it is re-derived on every read rather than frozen at capture.
- **The message limit** (`--max-chars`, default 3000) lives at `messages.finalize`.
  It stacks every block, measures the text, and appends one closing line naming
  the overrun when there is one. An over-long report is the complete report plus
  that line — never a shorter report.

Neither the tool nor `budget` can summarize: it takes no credentials and has no
model to ask. So the code marks the overrun and the summarization is done by
whoever writes the message out. `budget` exposes `overage(text, limit)` and
`fits_within(text, limit)` for that measurement, and nothing that selects a
shorter rendering.

Adding a block means adding one entry to the block list in `messages.py`, not an
`if` inside a renderer. A block that renders to nothing drops out by itself
(`stacked` already leaves out an empty block), so a conditional block needs no
branch of its own.

## Invariants

These are the rules the code exists to hold. A change that breaks one is a bug,
even when the tests still pass.

1. **Never merge two variants into one price row.** Delivery mode, region, time
   band, context tier, promotion, generation, and currency each keep their own
   row. A tidy comparison that averages them is wrong.
2. **Identify by content, never by a name list.** Tables are located by the
   wording of a model column, prices by the wording of a price header. Adding a
   model name to code so a parser recognizes it is the anti-pattern the whole
   `parsing` layer was written to remove.
3. **A header that describes the request is a condition, not a price.** A length
   band (`条件 输入长度：千 token`) or a peak/off-peak label still has to survive
   into `conditions`; reading it as a price column silently drops the tier that
   distinguishes rows.
4. **Read only amounts that name their own currency.** A page publishing both CNY
   and USD tables yields the adapter's own currency and nothing relabelled.
5. **Peak/off-peak windows are per platform and quoted verbatim.** Read the window
   from that vendor's own document into `time_bands`; `publishes_time_bands` gates
   it so a platform that states no window is never handed another platform's. A
   rule is selected by model name first, delivery label second — one page can bill
   two generations on different calendars.
6. **A description failure never touches a price result.** A missing, retired, or
   unreadable introduction is reported as `not_found` or `source_error`; neither
   status may suppress or alter a price that parsed fine.
7. **Never synthesize Tencent model text.** Its details sit behind an authenticated
   console, so the checked-in mirror is the only source. A model absent from it is
   `not_found` — not a cue to infer capabilities from a name.
8. **A failed or empty scan never overwrites a good baseline.** `source_error` and
   `empty_scan` are reported and the previous baseline is kept, so the change
   survives into the run after the source recovers.
9. **Cache and baseline are different things.** The cache is a 3-hour TTL store of
   responses, reused inside that window. A baseline never expires, because an
   expiring one would turn every run into a first run. `delta` therefore always
   reads the sources afresh — a cache hit would be handed back as "no change".
10. **Bump the schema version when a shape changes.** `CACHE_SCHEMA_VERSION` covers
    parsed responses (`CACHE_TTL`), `SNAPSHOT_SCHEMA_VERSION` covers baselines. A
    parser or source change requires the cache bump, or stale parsed data is served
    for the rest of its TTL; a baseline written by an older shape is treated as
    absent rather than diffed against.
11. **A length limit is met by summarizing, never by truncating.** Two limits
    exist. An introduction over 300 characters is kept whole and marked
    `summary_needs_condensing` (`descriptions.core.SUMMARY_MAX_CHARS`), and the
    label says so and names the original length. A message over `--max-chars`
    (default 3000) is rendered complete with one closing line naming the overrun;
    no block is dropped, no list is capped, no string is sliced. This is not
    cosmetic: a cut price loses its decimal point and the reader cannot tell what
    was left out. The tool takes no credentials and has no model, so it never
    summarizes either — it measures and says so, and whoever sends the message
    summarizes. Those are the only two length rules; nothing in `messages` may
    measure or slice a string, and `budget` only counts characters.
12. **The message carries no Markdown.** No `**`, no `#`, no tables, no links —
    hierarchy is numbering and indentation, and each source URL is written last
    on its line. DingTalk reads a document back through its own parser, which
    mangles a report this dense.
13. **The model introduction opens every message.** Both `comparison_blocks` and
    `scan_blocks` put it first, after the header and before the conclusion: a
    reader who does not know what a model is for cannot judge what it costs.
    Never move it below a price, and never print it per channel — what a model
    does is a property of the model, so a scan introduces each moved model once
    from `changed_descriptions` rather than once per channel that reported it.
    Its source line is never optional: an unsourced capability claim is not a
    fact. The message omits an `active` lifecycle because a newly listed model is
    necessarily available; preview, legacy, retired, and unknown remain visible.
14. **Statuses are reported, never softened.** `source_error`, `not_found`,
    `empty_scan`, `update_skipped`, `check_failed` all reach the reader. A provider
    that held still is still named, because that is what shows the scan covered it.
15. **No credentials, ever.** The only authenticated path is refreshing the
    explicit Tencent mirror through a user-owned logged-in session, offline.
16. **An official update time needs official evidence.** A vendor-labelled date or
    a matching official DeepSeek news page may populate `source_updated_at`. When
    no such evidence exists, the message labels the previous successful snapshot's
    `captured_at` as 上次更新时间; it never calls that fallback 官方更新时间. A
    date-only source remains date-only rather than acquiring an invented midnight.

## Where a change goes

| Change | Put it in |
| --- | --- |
| Add a price provider | a new module in `providers/`, then register it in `providers/__init__.py` |
| A vendor's page moved or its table changed | that vendor's `providers/*.py` constants, plus `references/source-notes.md` |
| Add a model-introduction source | `descriptions/sources.py` and `DESCRIPTION_SOURCE_CLASSES`; routing in `descriptions/resolver.py` |
| Change how one value reads | `reporting.py` |
| Change how a message is laid out | `messages.py` |
| Change which blocks a density keeps, or what opens a message | the section list in `messages.py` (`comparison_sections` / `scan_sections`) |
| Change the character budget or how a density is chosen | `budget.py`, plus `DEFAULT_MAX_CHARS` callers in `query_model_prices.py` |
| Change which providers a query covers | `registry.py` |
| Change the cache or snapshot shape | `paths.py` version, then the reader and writer together |
| Refresh the Tencent mirror | `scripts/update_tencent_model_mirror.py` |
| Change the CLI surface | `query_model_prices.py` — and `SKILL.md` and `README.md`, which both document it |

### Adding a price provider

Most vendors publish a documented table of per-model prices, so the work is
declaration rather than parsing:

```python
class XiaomiAdapter(TabularTokenPricingAdapter):
    provider_id = "xiaomi"
    provider_name = "小米 MiMo"
    source_url = XIAOMI_URL
    source_kind = "official_html"
    currency = "CNY"
    region = "中国区"
```

`TabularTokenPricingAdapter` walks the document once and holds the rows, so a
scan costs one read rather than one query per model. Override only the policy that
actually differs: `price_kind`, `cell_amount`, `offer_name`, `model_column`,
`model_conditions`, `record_extras`, or `model_extras`. Subclass `PriceSource`
directly only when the vendor's shape is genuinely not a table (`aliyun` reads a
JSON API, `tencent` reads embedded Slate JSON, `baidu` reads a Gatsby pre-fetch).

Two declarations deserve care: `carry_forward_model` for vendors that leave a
continuation row's model cell empty, and `publishes_time_bands` for vendors that
explain a peak/off-peak window in prose beside the table.

Override `catalog_records()` when the adapter can build every record from the
document it already parsed. The inherited default walks the whole catalogue with
one `query` per model, which is correct but pays an HTTP request per model for an
adapter whose `query` fetches on its own. Aliyun's public Qianwen model-market API
returns series with all their independently priced model items, so its adapter
loads that catalogue once and reuses it for listing, queries, and scans.

Prefer the representation the page publishes for machines — its own Markdown copy
when the tables are real Markdown tables, otherwise its public structured JSON.
Parse rendered HTML only when the vendor publishes neither.

## Conventions

- **Standard library only.** There is no requirements file and no `pyproject.toml`
  on purpose: the install story is `git clone` and run. A third-party import
  breaks it.
- `from __future__ import annotations` at the top of every module.
- Every module opens with a docstring saying *why it exists* and which constraint
  it holds — not an inventory of what it contains.
- Comments explain the reason at the point the rule is non-obvious. No comment
  restates the code under it.
- Type hints throughout. `Any` is for the loosely-shaped JSON payloads, where the
  shape is documented in `references/schema.md` instead.
- Names carry the documentation: `publishes_time_bands`, `carry_forward_model`,
  `NON_IDENTITY_CONDITIONS`, `describes_request_length`.
- Reach for a function; use a class for a contract (`PriceSource`,
  `DescriptionSource`) or for state that has to be carried across calls
  (`TabularTokenPricingAdapter._parsed_rows`, `CacheStore`).
- Change behaviour by overriding policy, not by copying a method into a subclass.
- Chinese text belongs only in strings a person reads: report wording, `region`
  values, and vendor-facing labels. Identifiers, comments, and docstrings are
  English.
- `query_model_prices.py` stays thin and its invocation surface stays stable:
  argument parsing, provider selection, rendering, exit code.

## Commands

```bash
python3 -m unittest discover -s tests                          # full suite, offline
python3 scripts/query_model_prices.py --help
python3 scripts/query_model_prices.py compare deepseek-flash --format json
python3 scripts/query_model_prices.py delta --format message
python3 scripts/query_model_prices.py compare deepseek-flash --format message --max-chars 1200
python3 scripts/update_tencent_model_mirror.py CAPTURE.json
```

Tests never touch the network. An adapter accepts any object with `get_text`, so
the suite passes a `MappingClient` that maps a URL to a fixture document (or to an
exception, to exercise a failure path). Add a vendor by adding its fixture and a
test that parses it, not by adding a live request.

## Runtime state

`cache/` and `snapshots/` hold local state that must not be committed, but the
**directories themselves must exist** because the script writes into them. Each
therefore carries its own `.gitignore` — `*` plus `!.gitignore` — rather than being
listed in the root `.gitignore`, which would drop the directory along with its
contents and leave a fresh clone with nowhere to write.

## The two checkouts

The repository may be cloned twice on a working machine: a development clone, and
an installed copy in the skill host's configured search path. They are independent
clones, so editing one changes nothing in the other until the change is committed,
pushed, and the other fast-forwards.

- The installed copy self-updates at the start of every `--refresh` and every
  `delta`: it fetches upstream, checks whether the incoming commits actually
  touched this skill, and only then fast-forwards and re-executes itself. A dirty
  working tree there turns the update into `update_skipped`.
- **Never install by copying.** A copy has no `.git`, so every refresh reports
  `check_failed`. A symlink to a real clone resolves to the repository and is fine.
- `snapshots/*.json` are not in git. A fresh clone has no baselines, so its first
  `delta` reports `baseline_created` for every provider instead of a comparison.
  Copy them over when that matters.

## Anti-patterns

- Restating `SKILL.md`'s runtime rules or `README.md`'s usage in a docstring.
  Point at the document instead.
- Teaching a parser a model name. Fix the rule it uses.
- Merging currencies, time bands, regions, or generations to make a comparison
  tidier, or reporting a price of zero for a model that is only billed per
  request, per second, or on a free tier. Say it is unpriced.
- Letting a missing source become an empty row or a silent omission.
- Truncating anything to fit a limit — a message, an introduction, a list, or a
  single line. Cut words, never content: summarize. Nor letting a length rule live
  anywhere but `budget.py`, `messages.finalize`, and the summary limit in
  `descriptions.core`.
- Printing a model's introduction once per channel, or below the price.
- Caching anything `delta` reads.
- Growing the CLI entry point past argument parsing and dispatch.
- Adding a dependency.
