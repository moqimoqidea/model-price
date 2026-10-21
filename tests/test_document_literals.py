"""Reject runtime expressions and unrelated page data before they become prices."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.errors import SourceError
from model_price.literals import literal_at
from model_price.mintlify import document_tables

URL = "https://example.test/docs/pricing/chat"
SOURCE = '_jsx(DocTable,{columns:[{title:`模型`},{title:`输入（元）`}],rows:[[`brand-new-model`,`¥1.23`]]})'


def page_source(source: str = SOURCE, *, size_delta: int = 0, unrelated: bool = False) -> str:
    """Keep UTF-8 lengths and record references in the same shape as the capture."""
    owner = {"mdxSource": {"compiledSource": "$a", "scope": {"pageMetadata": {"href": "/pricing/chat"}}}}
    chunks = [
        ':HL["/style.css","style"]\n',
        f"a:T{len(source.encode('utf-8')) + size_delta:x},",
        source[:27], source[27:],
        "b:" + json.dumps(owner, ensure_ascii=False) + "\n",
    ]
    if unrelated:
        other = SOURCE.replace("brand-new-model", "navigation-decoy")
        chunks += [f"c:T{len(other.encode('utf-8')):x},", other]
    return "".join("<script>self.__next_f.push(" + json.dumps([1, chunk], ensure_ascii=False) + ")</script>" for chunk in chunks)


class LiteralDocumentTests(unittest.TestCase):
    def test_literal_values_keep_currency_strings_escapes_and_numeric_precision(self) -> None:
        result = literal_at(r'''{text:`元\u002f百万`,rates:['¥1.23',.25,],enabled:!0,missing:void 0,}''')
        self.assertEqual(result, {"text": "元/百万", "rates": ["¥1.23", "0.25"], "enabled": True, "missing": None})

    def test_nonliteral_rates_and_template_interpolation_are_rejected(self) -> None:
        for expression in (
            '{rate:getRate()}', '[`¥${rate}`]', '{rate:other.rate}',
            '[...rates]', '{rate:voided}', '"¥1.23".replace("1","9")',
            '`fixed` + value', '{rate:1}.mutate()',
        ):
            with self.subTest(expression=expression), self.assertRaises(SourceError):
                literal_at(expression)

    def test_fragmented_utf8_data_keeps_owned_columns_and_excludes_other_records(self) -> None:
        tables = document_tables(page_source(unrelated=True), URL)
        self.assertEqual(tables, [[["模型", "输入（元）"], ["brand-new-model", "¥1.23"]]])

    def test_missing_or_mismatched_page_ownership_is_an_error(self) -> None:
        for old, new in (("/pricing/chat", "/pricing/another"), ("mdxSource", "mdxSourceWithNoJs"), ("$a", "$unknown")):
            with self.subTest(field=old), self.assertRaises(SourceError):
                document_tables(page_source().replace(old, new), URL)

    def test_text_lengths_are_bytes_and_incomplete_records_fail(self) -> None:
        for delta in (-1, 1, 999):
            with self.subTest(delta=delta), self.assertRaises(SourceError):
                document_tables(page_source(size_delta=delta), URL)

    def test_dynamic_or_missing_table_data_is_not_a_partial_price_success(self) -> None:
        for source in (
            SOURCE.replace('`¥1.23`', 'getRate()'),
            SOURCE.replace('`¥1.23`', '`¥${rate}`'),
            SOURCE.replace('columns:', 'captions:'),
            SOURCE.replace('rows:', 'unrelatedRows:'),
            SOURCE.removesuffix('})') + '}.mutate())',
        ):
            with self.subTest(source=source), self.assertRaises(SourceError):
                document_tables(page_source(source), URL)


if __name__ == "__main__":
    unittest.main()
