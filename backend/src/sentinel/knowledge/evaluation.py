"""Retrieval metrics for the labeled knowledge fixture."""

import math


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    found = set(ranked[:k]) & relevant
    return len(found) / len(relevant)


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    gains = [1.0 if item in relevant else 0.0 for item in ranked[:k]]
    ideal = [1.0] * min(len(relevant), k)
    denom = _dcg(ideal)
    if denom == 0:
        return 0.0
    return _dcg(gains) / denom


def citation_grounded(excerpt: str, content: str) -> bool:
    cleaned = excerpt.removesuffix("…").strip()
    return bool(cleaned) and cleaned in content


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
