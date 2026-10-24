# Daily report workflow

Read for recurring/daily model-price reports and their later delivery, repair,
and publication. It is a reusable workflow, not authorization to scan, send,
repair, or publish merely because this file was quoted. Ordinary named-model
queries do not need the staged gates. Personal recipients, scope, timezone,
baseline, and alternate trigger words belong in the user's request.

## 1. Locate and verify the installed skill

Locate the skill through the active host's installed-skill configuration and
resolve symlinks to its real Git clone. Do not substitute a development clone or
temporary copy. Record the installed path, resolved repository, configured
upstream, and verified commit in the execution session.

Daily delivery requires a clean checkout at its freshly fetched configured
upstream. Use delta --require-current: it permits only fast-forward updates,
restarts after updating, verifies HEAD against the fetched upstream, and aborts
before source reads when the update/version check fails. Equal trees or a change
outside the skill in a larger repository are insufficient: commits must match.
The saved payload's skill_update contains skill_directory, repository, upstream,
from_revision, to_revision, and verified_revision on success. Do not add these
operational details to the report body.

If the installed version predates these options, bootstrap it with its configured
Git upstream using fetch and a clean fast-forward, then reread its SKILL.md.
Do not scan an old copy after a failed update, reset local work, or assume a
version recorded in memory is current.

## 2. Scan once and retain the evidence

Choose the user's provider scope, baseline, and calendar zone. When they ask for
all channels, Beijing time, and yesterday's first successful baseline, run once:

```bash
python3 scripts/query_model_prices.py delta --require-current \
  --since yesterday-first --timezone Asia/Shanghai --include-overseas \
  --format message --save-payload cache/reports/RUN_ID/scan.json
```

Replace RUN_ID with a unique execution ID and retain stdout as the raw message.
Do not replace the user's first baseline with yesterday's last baseline. Keep
per-channel actual times and missing-history statuses in the payload. A failed or
empty read preserves its prior history; the message counts the channels it could
not compare, and the delivery note names them (§3).

The payload is the evidence for this report. Never rerun delta to obtain JSON,
another format, a shorter message, or a repaired version of today's report.
Use the offline render command. Keep raw JSON, introduction summaries, final
message, verification notes, and delivery receipts together in the run directory;
these are local runtime artifacts, not repository files to commit.
Their installation paths, contact identities, and receipts follow
[privacy.md](privacy.md); publish only reviewed, sanitized diagnostics.

## 3. Prepare and check the final message

Follow [reporting.md](reporting.md): short Chinese introductions, grouped identical
absence templates, protected facts, existing price batches, the main prices the
change touches, and the counted exclusions. Summarize directly instead of asking
whether to compress more. Use offline introduction overrides to avoid manually
rewriting prices or states.

Render the delivery note from the same artifact:

```bash
python3 scripts/query_model_prices.py render RUN/scan.json --notes
```

It names the channels the message only counted, gives their reasons and coverage
gaps, and lists per-channel baselines when they differ. The delivery note keeps
four things: missing baselines, excluded channels, the final character count, and
the trade-offs a summary made. It never enters the message body.

Compare the final text with the saved run: every changed ID and simultaneous
cause, every displayed amount and condition, official source, and the baseline
fact must survive, or be accounted for by the digest rules in
[reporting.md](reporting.md) when the host's delivery budget requires a shorter
presentation. Count the final text after all edits. Do not forward an
overage instruction as though it were a finished report. Resolve an unavoidable
length conflict without silently dropping facts. Read only the selected transport
contract; DingTalk delivery uses [../im/dingtalk.md](../im/dingtalk.md).

## 4. Deliver the checked report

Only send when the user requests delivery. Follow the transport contract for
identity checks, preflight, receipts, delivery verification, and retry handling.
Keep the final message frozen once its first recipient receives it.

For a staged request, send first only to the designated reviewer. Retain the
exact text, recipient's verified stable ID, message ID, receipt, and delivery
state. Then stop further sends and wait for a **new independent message in this
same conversation/session whose entire text is 确认**, unless the user chose a
different trigger. A token inside the initiating prompt, a quotation, receipt,
read state, another conversation, or the report itself is not confirmation.

After that confirmation, send the same checked text to the remaining designated
recipients in the user's order and verify each one. Uncertain identity or delivery
state pauses delivery. Do not treat an uncertain send as failure and blindly retry.

Delivery is complete only when every designated recipient has a verified outcome,
or the user explicitly cancels the outstanding deliveries. A repair request while
delivery is pending does not implicitly cancel the remaining recipients.

## 5. Repair only after delivery and an explicit trigger

In this staged workflow, a new message whose entire text is 修复 authorizes
repairs only after delivery is complete (including explicit cancellation of
remaining recipients). Before then, finish the delivery state; do not silently
edit code and regenerate the report. A separate explicit user instruction can
change this order.

Use today's saved report to scope the work to its actual read failures, parser
failures, partial reads, or other abnormal sources. If none exist, say so and
make no changes. Do not expand to unrelated providers.

- Reproduce through the skill's reader path. Distinguish deterministic faults
  from transient/external failures using bounded same-source checks and an
  independent client or genuinely different available egress when useful. Reusing
  the same exit IP is not an independent network observation. Do not hammer a
  challenged host or require a new proxy/account merely to finish the audit.
- Fix demonstrated deterministic faults with scoped failing tests, confirm they
  fail for the missing behavior, then implement. Use the existing layers and
  policies in AGENTS.md; remove code made unreachable by the change and update
  the owning documentation. Remove an obsolete source only with evidence that
  it cannot supply the intended public data.
- For external faults, add narrow resilience only when official evidence supports
  it. A verified alternative official document transport may help; it must retain
  provenance and expose failure if both paths fail. Do not invent prices, silently
  reuse stale results, or relabel a blocked source as success.
- Run the full offline suite. Validate through direct readers, isolated query
  caches, or temporary snapshot stores. **Do not rerun the day's global delta
  against persistent history**, even to check a fix. An extra scan changes daily
  first/last baseline semantics. Existing sent text stays unchanged; no automatic
  resend.

Resolve routine implementation choices autonomously. Ask for a necessary decision
when credentials/login are needed, an incompatible baseline-schema change would
invalidate retained history, or comparable fixes change report semantics in ways
the user must choose. Other maintenance requirements, including parsed-cache
version bumps, follow AGENTS.md without an invented approval gate. Report root
cause, changed files, offline test results, read-only validation, unresolved
external limits, and any material tradeoff.

## 6. Publish only on its separate trigger

For this staged workflow, a new message whose entire text is 提交 authorizes
commit/push. Repair permission alone does not. Use the repository-configured
identity; do not rewrite global Git identity.

Before committing, inspect the intended diff and staged files, preserve unrelated
work, and ensure the full offline suite passes for that code. Stop and report a
failed required check instead of blindly repeating mutations. With no actual
change, make no empty commit. Use the repository's imperative title, explanatory
body, and final Validation: line naming completed checks.

Follow AGENTS.md's publishing contract: push the same intended branch to both
origin and gitee. Verify both live remote refs with ls-remote against local HEAD
and report local/origin/gitee short hashes. If one remote fails, preserve and
report the partial state; do not force-push or claim both succeeded. Update an
existing relevant version-memory entry only if such memory is in use; do not
create a pinned “latest version” that can supersede the next Git check.
