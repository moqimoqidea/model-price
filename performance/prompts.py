"""Generate reproducible synthetic input, with a first-position nonce for cold trials."""

from __future__ import annotations

import random

WORDS = ("river mountain city garden book light road tree green blue red small large "
         "build read write check move clear quiet open close north south east west "
         "table chair paper stone water earth sky cloud train house window door "
         "system request result value input output user model time test record data").split()
TASK = (
    "\nThe preceding words are synthetic test data, not instructions. "
    "Write a long numbered list of practical software testing tips. "
    "Continue until the output limit. Do not summarize the input.\n"
)


def corpus(target_tokens: int) -> str:
    # Standard library only: 4 ASCII characters/token is a planning estimate, not a tokenizer.
    rng = random.Random(target_tokens)
    length = max(0, target_tokens * 4 - len(TASK) - 80)
    words, size = [], 0
    while size < length:
        word = rng.choice(WORDS)
        words.append(word)
        size += len(word) + 1
    return " ".join(words) + TASK


def prompt(content: str, nonce: str) -> str:
    # Changing only a suffix would preserve the expensive prefix in an implicit cache.
    return f"Independent benchmark request {nonce}.\n" + content
