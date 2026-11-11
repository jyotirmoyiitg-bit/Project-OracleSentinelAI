"""Tokenization shared by local embeddings and keyword ranking."""

import re

_TOKEN = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)


def tokenize(value: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN.finditer(value)]


def features(value: str) -> list[str]:
    """Word tokens plus adjacent pairs, so short technical phrases stay distinctive."""
    tokens = tokenize(value)
    pairs = [f"{tokens[index]}_{tokens[index + 1]}" for index in range(len(tokens) - 1)]
    return tokens + pairs
