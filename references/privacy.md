# Privacy and publication boundaries

Repository content is public; installed paths, raw console captures, personal
delivery preferences, recipient identities, receipts, and local debug logs are
not publication material. Use relative repository links in documentation and
synthetic values in tests. Store private run files in ignored runtime directories.

## Diagnostics

`privacy.py` owns diagnostic redaction. Source and updater exceptions, generic
price/query errors, introductions, retirement reads, publication errors, and
Mistral field failures use it before an error can enter a shared report.
Typed HTTP errors also sanitize their exposed URL. Offline rendering sanitizes
diagnostics from older saved scans without rewriting the original artifact.

Redaction covers authorization values, cookies, known credential/token formats,
credentials in URL user information, sensitive query parameters (including
percent-encoded field names), home/temporary paths, email addresses, and DingTalk
contact IDs. Public model IDs, non-sensitive URL parameters, HTTP status codes,
retry counts, and routing diagnostics remain useful. Repeated redaction is
idempotent. It applies to diagnostics, not official price amounts or quotations.

Full tracebacks and local Git configuration can still contain private context;
keep them local rather than pasting them into a report or public issue. The saved
scan's `skill_update` installation/repository paths are deliberate local version
evidence and do not appear in the scan message. Review a JSON artifact before
sharing it outside its originating session.

## Tencent captures

Both console-metadata and legacy `models[]` imports are checked before model
normalization, including their top-level and nested fields. Credentials, account
identifiers, contact fields, private URLs, and freeform credential headers cause
rejection with a generic error; their values are never echoed. Runtime mirror
validation uses the same rule. Explicit official API placeholders such as
`Authorization: Bearer YOUR_API_KEY` remain allowed as example prose.

This keeps authenticated account data out of the checked-in model mirror while
preserving the channel's public model facts. A new source capture still needs
review: arbitrary names or unlabelled private prose cannot be reliably classified
by a pattern matcher. Never publish a raw logged-in browser export.

The parsed-cache version is 36 so cached introductions cannot bypass the updated
validation. Price, lifecycle, and publication baseline versions are unchanged.

## Local artifacts and Git

`cache/` and `snapshots/` retain their own ignore files. Environment overrides,
HAR exports, key/certificate containers, and private capture/session directories
are ignored at the root too. Ignore rules do not protect a file that was already
tracked or explicitly force-added: review the staged diff before publishing.
`write_json` creates private temporary files and atomically replaces its target.
Use private filesystem permissions for separately written delivery ledgers.

Published history is separate from the current tree. A normal corrective commit
does not erase old objects or external copies. Rewriting a public branch requires
a deliberate migration because installed clones rely on fast-forward updates.
Do not force-push as an implicit part of ordinary publication.

## Audit on 2026-10-16

The audit covered all publishable working-tree files, 763 unique blobs reachable
from local branches/remote-tracking refs, and reachable commit messages. It found
16 workstation-specific links in the previous OpenAI audit; the current document
uses relative links. No real API key, private key, personal contact detail, or
DingTalk recipient ID was found in that scope. A Cookie match was a synthetic
non-secret HTTP test value. Existing Git attribution remains the configured public
project identity rather than application data.

The user chose normal publication and retention of previous commits. The old
workstation links therefore remain in historical objects; this correction does
not claim to remove them from published history.

Offline regressions reproduced the unsafe exception/capture paths before the
fix. They check redaction, useful failure facts, unchanged successful baselines,
both capture formats, nested private fields, official placeholders, and rendering
older artifacts without modifying their source files. The audit did not inspect
or publish private ignored run files, browser credentials, or account stores.
