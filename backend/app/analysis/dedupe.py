"""Keep suggestions distinct: no two clips of the same footage, no retelling of the same story twice,
and nothing that already exists as a clip of this video."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .scoring import Scored

MAX_OVERLAP = 0.10          # share of the shorter clip two suggestions may have in common
MAX_OVERLAP_SECONDS = 1.0   # ... and never more than this many seconds
MAX_SIMILARITY = 0.55       # share of distinctive words in common before it counts as the same moment


@dataclass(frozen=True, slots=True)
class ExistingClip:
    start: float
    end: float
    tokens: frozenset[str]


def time_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Shared time as a fraction of the shorter of the two ranges."""
    shared = min(a_end, b_end) - max(a_start, b_start)
    shorter = min(a_end - a_start, b_end - b_start)
    return max(0.0, shared) / shorter if shorter > 0 else 0.0


def text_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of the distinctive words."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_distinct(scored: Sequence[Scored], count: int, *, existing: Sequence[ExistingClip] = (),
                    min_score: float = 0.0, uniqueness_weight: float = 0.03) -> list[Scored]:
    """Best first; skip anything that overlaps or repeats a better pick or an existing clip.

    Each pick gets its "uniqueness" sub-score: 100 when nothing similar was picked before it, lower the more
    words it shares with a better pick or an existing clip.
    """
    picked: list[Scored] = []
    for cand in sorted(scored, key=lambda c: -c.score):
        if len(picked) >= count or cand.score < min_score:
            break
        nearest = 0.0
        clash = False
        for other in (*picked, *existing):
            shared = min(cand.end, other.end) - max(cand.start, other.start)
            if shared > MAX_OVERLAP_SECONDS or time_overlap(cand.start, cand.end, other.start, other.end) > MAX_OVERLAP:
                clash = True
                break
            similarity = text_similarity(cand.tokens, other.tokens)
            if similarity > MAX_SIMILARITY:
                clash = True
                break
            nearest = max(nearest, similarity)
        if clash:
            continue
        uniqueness = round(100.0 - 100.0 * nearest, 1)
        adjusted = round(cand.score - uniqueness_weight * (100.0 - uniqueness), 1)
        if adjusted < min_score:
            continue
        picked.append(replace(cand, score=adjusted, subscores={**cand.subscores, "uniqueness": uniqueness}))
    picked.sort(key=lambda c: -c.score)
    return picked
