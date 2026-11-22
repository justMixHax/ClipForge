"""Turn the user's stored feedback into small, transparent adjustments to ranking.

Nothing here is a black box: each label nudges named score dimensions by a fixed, capped amount, and good
or bad marks on a category shift that category by a few points. The current adjustments are shown in the
app so the user can see exactly what their feedback changed.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

# label -> {dimension: how much more that dimension should matter, per piece of feedback}
LABEL_EFFECTS: dict[str, dict[str, float]] = {
    "too_much_context": {"clarity": 0.12},
    "wrong_moment": {"payoff": 0.08, "hook": 0.06},
    "not_entertaining": {"entertainment": 0.10, "emotion": 0.04},
    "poor_subtitles": {"subtitles": 0.10},
    "bad_cropping": {},  # informs layout defaults, not ranking
    "good": {},
    "bad": {},
}
NEGATIVE = {"bad", "not_entertaining", "wrong_moment", "too_much_context"}
MAX_BOOST = 0.5
MAX_BIAS = 12.0
BIAS_STEP = 2.5


@dataclass
class FeedbackProfile:
    dimension_boost: dict[str, float] = field(default_factory=dict)
    category_bias: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_labels(cls, items: Iterable[str | tuple[str, str]]) -> FeedbackProfile:
        """items: feedback labels, optionally paired with the category of the clip they were given on."""
        boost: Counter[str] = Counter()
        net: Counter[str] = Counter()
        counts: Counter[str] = Counter()
        for item in items:
            label, category = (item, "") if isinstance(item, str) else item
            counts[label] += 1
            for dim, amount in LABEL_EFFECTS.get(label, {}).items():
                boost[dim] += amount
            if category:
                if label == "good":
                    net[category] += 1
                elif label in NEGATIVE:
                    net[category] -= 1
        return cls(
            dimension_boost={dim: min(MAX_BOOST, value) for dim, value in boost.items()},
            category_bias={cat: max(-MAX_BIAS, min(MAX_BIAS, BIAS_STEP * n)) for cat, n in net.items() if n},
            counts=dict(counts),
        )

    def summary(self) -> list[str]:
        """Plain-language list of what the feedback currently changes."""
        lines = [f"{dim.capitalize()} matters {round(boost * 100)}% more" for dim, boost in
                 sorted(self.dimension_boost.items(), key=lambda kv: -kv[1]) if boost > 0]
        for cat, bias in sorted(self.category_bias.items(), key=lambda kv: -kv[1]):
            lines.append(f"'{cat}' clips rank {'higher' if bias > 0 else 'lower'} ({bias:+.0f} relevance)")
        return lines
