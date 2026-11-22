"""Content presets (how to weigh the 12 score dimensions) and workflow presets (one-click setups)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .feedback import FeedbackProfile

DIMENSIONS = ("hook", "entertainment", "emotion", "clarity", "visual", "pacing", "payoff", "shareability",
              "relevance", "subtitles", "originality", "uniqueness")

DIMENSION_LABELS = {
    "hook": "Opening hook", "entertainment": "Entertainment", "emotion": "Emotional impact",
    "clarity": "Clear without context", "visual": "Visual interest", "pacing": "Pacing",
    "payoff": "Payoff", "shareability": "Shareability", "relevance": "Audience relevance",
    "subtitles": "Subtitle readability", "originality": "Originality", "uniqueness": "Not repetitive",
}

GAMING_WORDS = ["game", "level", "boss", "enemy", "enemies", "map", "loot", "health", "respawn", "ranked", "kill",
                "killed", "kills", "died", "dead", "shot", "jump", "glitch", "bug", "mission", "quest", "round",
                "lobby", "squad", "team", "weapon", "gun", "sniper", "ammo", "spawn", "damage", "hp", "win", "lose"]
GAME_STYLES: dict[str, dict] = {
    "gta": {"label": "GTA", "hashtag": "#gta",
            "words": ["cops", "police", "wanted", "heist", "car", "chase", "wasted", "busted", "stars", "vice city",
                      "los santos", "mission", "helicopter", "bank"]},
    "dayz": {"label": "DayZ", "hashtag": "#dayz",
             "words": ["zombie", "zombies", "infected", "base", "raid", "bandit", "sniper", "geared", "server",
                       "tent", "loot", "survivor", "freshie"]},
    "fc": {"label": "EA Sports FC", "hashtag": "#eafc",
           "words": ["goal", "penalty", "pack", "ultimate team", "weekend league", "rivals", "keeper", "assist",
                     "offside", "red card", "extra time", "striker", "header"]},
    "roblox": {"label": "Roblox", "hashtag": "#roblox",
               "words": ["obby", "robux", "admin", "tycoon", "simulator", "noob", "blox", "avatar"]},
    "other": {"label": "Other game", "hashtag": "", "words": []},
}
REACTION_WORDS = ["chat", "bro", "dude", "guys", "wait", "look", "what", "stop", "run", "help", "security",
                  "outside", "crowd", "police"]
CONVERSATION_WORDS = ["think", "opinion", "reason", "problem", "truth", "question", "answer", "story", "lesson",
                      "advice", "mistake", "learned", "experience", "believe", "people", "why", "because", "point"]

CONTENT_PRESETS: dict[str, dict] = {
    "auto": {
        "label": "Automatic",
        "description": "Balanced weights. Use when the video mixes talking and gameplay.",
        "weights": {"hook": .16, "entertainment": .15, "emotion": .08, "clarity": .12, "visual": .04, "pacing": .08,
                    "payoff": .14, "shareability": .09, "relevance": .04, "subtitles": .03, "originality": .04,
                    "uniqueness": .03},
        "keywords": GAMING_WORDS + REACTION_WORDS + CONVERSATION_WORDS,
        "layout": "auto", "captions": "opus",
    },
    "gaming": {
        "label": "Gaming",
        "description": "Clutches, fails, rage and funny gameplay. Action in the picture counts for more.",
        "weights": {"hook": .14, "entertainment": .17, "emotion": .11, "clarity": .09, "visual": .09, "pacing": .07,
                    "payoff": .13, "shareability": .07, "relevance": .06, "subtitles": .02, "originality": .02,
                    "uniqueness": .03},
        "keywords": GAMING_WORDS,
        "layout": "auto", "captions": "gaming",
    },
    "streamer": {
        "label": "Streamer / IRL",
        "description": "Reactions, unexpected situations and funny interactions.",
        "weights": {"hook": .15, "entertainment": .16, "emotion": .11, "clarity": .10, "visual": .05, "pacing": .07,
                    "payoff": .14, "shareability": .09, "relevance": .05, "subtitles": .02, "originality": .03,
                    "uniqueness": .03},
        "keywords": REACTION_WORDS + GAMING_WORDS[:10],
        "layout": "auto", "captions": "opus",
    },
    "podcast": {
        "label": "Podcast / interview",
        "description": "Hot takes, surprising answers and short stories. Clarity and a quotable line count for more.",
        "weights": {"hook": .17, "entertainment": .10, "emotion": .06, "clarity": .16, "visual": .01, "pacing": .08,
                    "payoff": .14, "shareability": .13, "relevance": .04, "subtitles": .04, "originality": .05,
                    "uniqueness": .02},
        "keywords": CONVERSATION_WORDS,
        "layout": "fill", "captions": "minimal",
    },
}

LENGTH_RANGES: dict[str, tuple[float, float]] = {
    "10-20": (10, 20), "20-40": (20, 40), "40-60": (40, 60), "60-90": (60, 90), "15-60": (15, 60),
}

# One-click setups shown in the AI Clipper. Saving your own is planned (the presets table exists, no UI yet).
WORKFLOW_PRESETS: dict[str, dict] = {
    "fast_gaming": {
        "label": "Fast Gaming Clips", "description": "Quick turnaround: short clips, fastest encode.",
        "config": {"content_preset": "gaming", "count": 5, "length": "15-60", "min_score": 55, "aspect": "9:16",
                   "layout": "auto", "captions": "gaming", "quality": "speed", "auto_render": True},
    },
    "hq_shorts": {
        "label": "High-Quality Shorts", "description": "Fewer clips, best encode quality.",
        "config": {"content_preset": "auto", "count": 3, "length": "20-40", "min_score": 65, "aspect": "9:16",
                   "layout": "auto", "captions": "opus", "quality": "quality", "auto_render": True},
    },
    "funny_streamer": {
        "label": "Funny Streamer Moments", "description": "Reactions and jokes from streams and IRL.",
        "config": {"content_preset": "streamer", "count": 5, "length": "15-60", "min_score": 60, "aspect": "9:16",
                   "layout": "auto", "captions": "opus", "quality": "balanced", "auto_render": True},
    },
    "podcast": {
        "label": "Podcast Highlights", "description": "Longer talking clips with clean captions.",
        "config": {"content_preset": "podcast", "count": 5, "length": "40-60", "min_score": 60, "aspect": "9:16",
                   "layout": "fill", "captions": "minimal", "quality": "balanced", "auto_render": True},
    },
    "max_candidates": {
        "label": "Maximum Engagement Candidates",
        "description": "Many candidates to choose from. Nothing is rendered until you pick.",
        "config": {"content_preset": "auto", "count": 12, "length": "15-60", "min_score": 45, "aspect": "9:16",
                   "layout": "auto", "captions": "opus", "quality": "balanced", "auto_render": False},
    },
    "manual": {
        "label": "Fully Manual Editing",
        "description": "Transcribe and suggest moments only. You trim, caption and render each clip yourself.",
        "config": {"content_preset": "auto", "count": 8, "length": "15-60", "min_score": 0, "aspect": "9:16",
                   "layout": "auto", "captions": "opus", "quality": "balanced", "auto_render": False},
    },
}


def effective_weights(preset: str, profile: FeedbackProfile | None) -> dict[str, float]:
    """Preset weights, shifted by what the user's feedback says matters, always summing to 1."""
    base = dict(CONTENT_PRESETS.get(preset, CONTENT_PRESETS["auto"])["weights"])
    if profile is not None:
        for dim, boost in profile.dimension_boost.items():
            if dim in base:
                base[dim] *= 1.0 + boost
    total = sum(base.values())
    return {dim: value / total for dim, value in base.items()}
