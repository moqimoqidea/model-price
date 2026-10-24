"""Keep vendor exporter changes from turning readable evidence into source errors."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.announcements.sources import ZHIPU_UPDATES_URL, _read_zhipu_updates
from model_price.delta import scan_providers
from model_price.errors import SourceError
from model_price.lifecycle import scan_lifecycle
from model_price.lifecycle_sources import SOURCES, minimax_events, read_events
from model_price.providers.kimi import KIMI_INDEX_URL, KimiAdapter
from model_price.snapshots import SnapshotStore, parse_baseline_selection, require_moment

FIXTURES = Path(__file__).parent / "fixtures"
KIMI_CHAT_URL = "https://platform.kimi.com/docs/pricing/chat"
KIMI_BATCH_URL = "https://platform.kimi.com/docs/pricing/batch"


def hosted_page(source: str, href: str) -> str:
    """Wrap literal MDX in the page-owned Flight records a Mintlify page serves."""
    owner = {"mdxSource": {"compiledSource": "$a", "scope": {"pageMetadata": {"href": href}}}}
    chunks = [
        f"a:T{len(source.encode('utf-8')):x},",
        source,
        "b:" + json.dumps(owner, ensure_ascii=False) + "\n",
    ]
    return "".join(
        "<script>self.__next_f.push(" + json.dumps([1, chunk], ensure_ascii=False) + ")</script>"
        for chunk in chunks
    )


class SavedClient:
    """Reject undeclared requests, including unnecessary alternate representations."""

    def __init__(self, documents: dict[str, str]) -> None:
        self.documents = documents
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        return self.documents[url]


class SourceFormatTests(unittest.TestCase):
    reference = require_moment("2026-10-21T15:00:00+08:00")

    def kimi_client(self) -> SavedClient:
        return SavedClient({
            KIMI_INDEX_URL: (
                f"- [模型价格]({KIMI_CHAT_URL}.md)\n"
                f"- [批量价格]({KIMI_BATCH_URL}.md)\n"
                "- [工具价格](https://platform.kimi.com/docs/pricing/websearch.md)\n"
            ),
            KIMI_CHAT_URL: (FIXTURES / "kimi-chat-components.html").read_text(),
            KIMI_BATCH_URL: (FIXTURES / "kimi-batch-components.html").read_text(),
            KIMI_CHAT_URL + ".md": "# 模型价格\n<table><thead><tr /></thead><tbody /></table>",
            KIMI_BATCH_URL + ".md": "# 批量价格\n<table><thead><tr /></thead><tbody /></table>",
        })

    def test_kimi_reads_the_pages_own_component_data_once_and_keeps_tiers(self) -> None:
        client = self.kimi_client()
        adapter = KimiAdapter(client)
        records = {item["model_id"]: item for item in adapter.catalog_records()}
        self.assertEqual(set(records), {"kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"})
        prices = records["kimi-k3"]["offers"][0]["prices"]
        self.assertEqual(
            [(p["type"], p["amount"]) for p in prices],
            [("cache_write", "20.00"), ("cache_write", "40.00"), ("cache_hit", "2.00"), ("input", "20.00"), ("output", "100.00")],
        )
        self.assertIn("5min", prices[0]["label"])
        self.assertIn("1h", prices[1]["label"])
        offers = records["kimi-k2.7-code"]["offers"]
        self.assertEqual([offer["name"] for offer in offers], ["online_standard", "batch"])
        self.assertEqual(next(p["amount"] for p in offers[1]["prices"] if p["type"] == "output"), "16.20")
        self.assertEqual(records["kimi-k3"]["source"]["url"], KIMI_CHAT_URL)
        self.assertEqual(adapter.query("kimi-k3")[0]["offers"], records["kimi-k3"]["offers"])
        self.assertEqual(client.calls, [KIMI_INDEX_URL, KIMI_CHAT_URL, KIMI_BATCH_URL])

    def test_kimi_reads_jsx_wrapped_table_titles_and_model_links(self) -> None:
        source = (
            '_jsx(DocTable,{columns:[{title:`模型`,width:`50%`},'
            '{title:_jsx(Tooltip,{tip:`命中缓存的请求按此价格计费`,children:`缓存命中`}),width:`50%`}],'
            'rows:[[_jsxs(_components.a,{href:`/guide/kimi-k9`,children:[`kimi-`,`k9`]}),'
            '`¥1.30 / MTok`]]})'
        )
        client = SavedClient({
            KIMI_INDEX_URL: (
                f"- [模型价格]({KIMI_CHAT_URL}.md)\n"
                f"- [批量价格]({KIMI_BATCH_URL}.md)\n"
            ),
            KIMI_CHAT_URL: hosted_page(source, "/pricing/chat"),
            KIMI_BATCH_URL: "<!doctype html><html></html>",
        })
        records = {item["model_id"]: item for item in KimiAdapter(client).catalog_records()}
        self.assertEqual(set(records), {"kimi-k9"})
        self.assertEqual(
            [(p["type"], p["amount"]) for p in records["kimi-k9"]["offers"][0]["prices"]],
            [("cache_hit", "1.30")],
        )

    def test_kimi_dynamic_or_mismatched_component_data_cannot_replace_a_baseline(self) -> None:
        first, second = "2026-10-20T12:00:00+08:00", "2026-10-21T12:00:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            good = scan_providers([KimiAdapter(self.kimi_client())], store, captured_at=first)
            self.assertEqual(good["providers"][0]["status"], "baseline_created")
            archived = store.read("kimi")
            for old, new in (("/pricing/chat", "/pricing/unrelated"), ("¥20.00", "${unreadableRate}")):
                client = self.kimi_client()
                client.documents[KIMI_CHAT_URL] = client.documents[KIMI_CHAT_URL].replace(old, new)
                failed = scan_providers([KimiAdapter(client)], store, captured_at=second)
                self.assertEqual(failed["providers"][0]["status"], "source_error")
                self.assertEqual(store.read("kimi"), archived)

    def test_zai_rendered_updates_keep_card_subjects_dates_and_bullet_prose(self) -> None:
        text = (FIXTURES / "zai-updates-rendered.html").read_text()
        client = SavedClient({ZHIPU_UPDATES_URL: text})
        found = _read_zhipu_updates(client, self.reference)
        self.assertEqual([(item["model_id"], item["published_at"]) for item in found], [("GLM-5.3-Flash", "2026-08-26"), ("GLM-5.3", "2026-08-18")])
        self.assertIn("visual", found[0]["summary"].lower())
        self.assertNotIn("Mythos 5", [item["model_id"] for item in found])
        self.assertEqual(client.calls, [ZHIPU_UPDATES_URL])

    def test_zai_incomplete_cards_fail_instead_of_dropping_one_release(self) -> None:
        text = (FIXTURES / "zai-updates-rendered.html").read_text()
        for old, new in (("update-description", "unreadable-description"), ("update-label", "unreadable-label")):
            with self.subTest(field=old), self.assertRaises(SourceError):
                _read_zhipu_updates(SavedClient({ZHIPU_UPDATES_URL: text.replace(old, new, 1)}), self.reference)

    def test_minimax_quoted_legacy_tables_and_free_music_notice_keep_their_scope(self) -> None:
        text = (FIXTURES / "minimax-models-quoted.md").read_text()
        client = SavedClient({SOURCES["minimax"]: text})
        _, found, _ = read_events("minimax", client, [])
        legacy = [item for item in found if item["notice_status"] == "legacy"]
        shutdown = [item for item in found if item["eos_at"]]
        self.assertEqual(len(legacy), 8)
        self.assertTrue(all(item["eos_at"] is None for item in legacy))
        self.assertEqual({item["model_id"] for item in shutdown}, {"Music-3.0-free", "Music-2.6-free", "music-cover-free"})
        self.assertTrue(all(item["eos_at"] == "2026-08-20" and item["scope"] == "MiniMax free music API" for item in shutdown))
        self.assertNotIn("music-3.0", {item["model_id"] for item in found})

    def test_minimax_incomplete_notice_cannot_be_hidden_by_readable_legacy_tables(self) -> None:
        text = (FIXTURES / "minimax-models-quoted.md").read_text()
        with self.assertRaises(SourceError):
            minimax_events(text.replace("August 20, 2026", "an unannounced date"))

    def test_minimax_legacy_tables_must_remain_readable_and_code_examples_are_inert(self) -> None:
        text = (FIXTURES / "minimax-models-quoted.md").read_text()
        with self.assertRaises(SourceError):
            minimax_events(text.replace("> | **Models**", "> | **Unreadable**", 1))
        self.assertEqual(minimax_events("```markdown\n" + text + "\n```"), [])

    def test_minimax_unreadable_sources_keep_the_previous_notice_archive(self) -> None:
        text = (FIXTURES / "minimax-models-quoted.md").read_text()
        adapter = SimpleNamespace(provider_id="minimax", provider_name="MiniMax")
        selection = parse_baseline_selection(None)
        first = "2026-10-20T12:00:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            good = scan_lifecycle(adapter, SavedClient({SOURCES["minimax"]: text}), None, store, first, selection, require_moment(first))
            self.assertEqual(good["status"], "baseline_created")
            archived = store.read("lifecycle-minimax")
            failed = scan_lifecycle(adapter, SavedClient({SOURCES["minimax"]: "# Models\nNo readable notices."}), None, store, self.reference.isoformat(), selection, self.reference)
            self.assertEqual(failed["status"], "source_error")
            self.assertEqual(store.read("lifecycle-minimax"), archived)


if __name__ == "__main__":
    unittest.main()
