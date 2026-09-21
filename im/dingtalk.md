# DingTalk delivery

Read this file only when a model-price message will be sent through DingTalk.
It is the single source of truth for DingTalk transport compatibility; keep the
report's content and summarization rules in `SKILL.md` and keep DWS-specific
versions, commands, and verification here.

## Delivery contract

- Generate or rewrite the report as `--format message`. Send the resulting plain
  text, preserving its internal newlines and blank lines.
- Use DWS **v1.0.62 or later on the stable release track**. Version 1.0.61 and
  v1.0.62-beta.6 or earlier accept `--msg-type text` on `+messages-send` but route
  it downstream as `msgType: "markdown"`. The fix first appeared in
  v1.0.62-beta.7 and is included in the v1.0.62 stable release.
- Use the `chat +messages-send` shortcut, including the leading `+`, with
  `--identity user`, `--msg-type text`, and `--text`. Do not substitute
  `chat message send` (without `+`) or `chat +dm`: both can use the Markdown
  transport even when their surface looks equivalent.
- Resolve the recipient to a stable ID before sending. Never select the first
  display-name match. If the only match is the authenticated DWS user, disclose
  that the destination is the sender's own direct chat when that is not already
  explicit in the request.
- A successful send receipt proves acceptance, not rendering. When delivery
  semantics are uncertain, inspect a dry-run before sending; do not use probe
  messages in a real conversation as the first diagnostic.

## Preflight

Check the binary that will actually execute:

```bash
dws --version
```

If it is older than v1.0.62, stop before sending and recommend an upgrade to the
current stable release. Do not upgrade the user's CLI unless that change is in
scope. The built-in upgrade path is:

```bash
dws upgrade --version v1.0.62
```

For an unfamiliar installation or a later version whose behavior has not yet
been observed, preview the exact shortcut with the real target and a harmless
multiline body:

```bash
dws chat +messages-send --identity user \
  --open-dingtalk-id <recipient-openDingTalkId> \
  --msg-type text --text $'A\nB\n\n- C\n- D' \
  --dry-run -y -f json
```

The preview must show `actions[0].arguments.msgType` as `text`, with content
shaped like `{"content":"A\nB\n\n- C\n- D"}`. If it reports `markdown`, do not
send and do not disguise the transport problem by inserting a blank line after
every source line. Report the version and the exact command path instead.

## Send

For a direct chat:

```bash
dws chat +messages-send --identity user \
  --open-dingtalk-id <recipient-openDingTalkId> \
  --msg-type text --text "$(cat message.txt)" -y
```

For a group, replace the direct target with its stable conversation ID:

```bash
dws chat +messages-send --identity user \
  --group <openConversationId> \
  --msg-type text --text "$(cat message.txt)" -y
```

The quoted command substitution preserves the report's internal line breaks;
the generated reports do not rely on a trailing newline. Use `-y` only after the
recipient and final message have been authorized.

## Rendering and limits

DingTalk's Markdown transport parses a single newline as a space. It preserves a
blank line as a rendered break and can join `- a\n- b` into `- a- b`; that is why
adding no Markdown syntax to the report is not sufficient by itself — the
transport must also be `text`.

A DingTalk text message accepts at most 5120 characters. Model-price defaults to
`--max-chars 3000` to leave room for growth and last-mile edits. If the report
marks itself over budget, summarize it according to [SKILL.md](../SKILL.md)
before sending: shorten prose, never truncate a string or remove a channel,
amount, condition, time window, source, or status.

If a v1.0.62-or-later dry-run shows `text` but the message read back by its exact
message ID is `markdown`, preserve the dry-run and readback evidence and report a
DWS or DingTalk backend regression. Do not silently rewrite the report around
that mismatch.
