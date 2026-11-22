"""Word and phrase cues the scorer looks for in what was said (English).

These are signals, not verdicts: each cue nudges one or two sub-scores, and the scorer combines them with
structure (where the clip starts and ends), delivery (audio) and picture (visual). For other languages
the cue lists simply do not fire, and the scorer leans on the non-text signals with lower confidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)
_WS = re.compile(r"\s+")
_LAUGH = re.compile(r"[\[(*]\s*(laugh\w*|chuckl\w*|giggl\w*)", re.I)
_STRIP = ".,!?;:\"'()[]{}…-–—“”‘’"

# category -> [(phrase, weight)]
CUES: dict[str, list[tuple[str, float]]] = {
    "hook": [
        ("can i tell you", 1.5), ("let me tell you", 1.5), ("i have to tell you", 1.3), ("you won't believe", 1.6),
        ("you will not believe", 1.6), ("watch this", 1.3), ("look at this", 1.1), ("check this out", 1.2),
        ("here's the thing", 1.2), ("here is the thing", 1.2), ("the truth is", 1.2), ("nobody talks about", 1.4),
        ("no one talks about", 1.4), ("the dumbest", 1.2), ("the craziest", 1.2), ("the funniest", 1.2),
        ("the scariest", 1.1), ("the worst", 1.0), ("the best", 0.8), ("the biggest", 0.8), ("guess what", 1.2),
        ("story time", 1.3), ("did you know", 1.2), ("have you ever", 1.0), ("what if", 0.9), ("imagine", 0.8),
        ("hot take", 1.5), ("unpopular opinion", 1.6), ("wait", 0.8), ("hold on", 0.7), ("listen", 0.7),
        ("secret", 0.8), ("biggest mistake", 1.1), ("this is why", 1.0), ("the reason", 0.6), ("how i", 0.6),
    ],
    "humor": [
        ("ha ha ha", 1.6), ("ha ha", 1.0), ("hahaha", 1.4), ("haha", 1.0), ("lol", 0.8), ("lmao", 1.0),
        ("hilarious", 1.0), ("funniest", 1.0), ("funny", 0.7), ("joking", 0.7), ("joke", 0.7), ("kidding", 0.7),
        ("laughing", 0.9), ("i'm dying", 1.0), ("i am dying", 1.0), ("dumbest", 0.9), ("stupidest", 0.9),
        ("embarrassing", 0.8), ("awkward", 0.6), ("bruh", 0.6), ("clown", 0.6),
    ],
    "excitement": [
        ("no way", 1.2), ("oh my god", 1.2), ("oh my gosh", 1.1), ("omg", 1.0), ("let's go", 1.2), ("lets go", 1.2),
        ("holy", 0.9), ("going absolutely insane", 1.3), ("going insane", 1.1), ("insane", 1.0), ("craziest", 1.1),
        ("crazy", 0.8), ("unbelievable", 1.0), ("incredible", 0.9), ("amazing", 0.7), ("what the", 0.9),
        ("are you kidding", 1.1), ("i can't believe", 1.1), ("i cannot believe", 1.1), ("wow", 0.8), ("oh no", 0.8),
        ("woo", 0.8), ("yes yes", 0.9), ("dude", 0.4), ("bro", 0.3),
    ],
    "surprise": [
        ("plot twist", 1.3), ("turns out", 1.0), ("out of nowhere", 1.1), ("all of a sudden", 0.9), ("suddenly", 0.8),
        ("the whole time", 1.0), ("the entire time", 1.0), ("didn't expect", 1.0), ("did not expect", 1.0),
        ("never expected", 1.0), ("never seen", 0.9), ("wait what", 1.1), ("what just happened", 1.2),
        ("how is that possible", 1.1), ("i was not ready", 1.0), ("no idea", 0.6), ("actually", 0.3),
    ],
    "conflict": [
        ("you are wrong", 1.2), ("you're wrong", 1.2), ("that's wrong", 1.0), ("i disagree", 1.1),
        ("terrible opinion", 1.3), ("worst take", 1.2), ("bad take", 1.1), ("shut up", 1.0),
        ("that makes no sense", 1.0), ("you're crazy", 0.9), ("are you serious", 0.9), ("die on this hill", 1.3),
        ("that's a lie", 1.0), ("not true", 0.9), ("fight me", 1.0), ("nonsense", 0.8), ("ridiculous", 0.7),
        ("argue", 0.6), ("debate", 0.5),
    ],
    "story": [
        ("long story short", 1.1), ("true story", 1.1), ("happened to me", 1.0), ("the other day", 1.0),
        ("one time", 0.9), ("when i was", 0.9), ("so i was", 0.8), ("i remember", 0.8), ("guess who", 0.8),
        ("last night", 0.7), ("years ago", 0.7), ("yesterday", 0.6), ("last week", 0.6), ("i spent", 0.6),
        ("and then", 0.4),
    ],
    "hot_take": [
        ("unpopular opinion", 1.5), ("hot take", 1.5), ("people will be mad", 1.2),
        ("i don't care what anyone says", 1.3), ("overrated", 1.2), ("underrated", 1.2), ("worst game", 1.0),
        ("best game", 1.0), ("dead game", 1.0), ("is not better", 0.9), ("isn't better", 0.9), ("is trash", 0.9),
        ("is mid", 0.9), ("nobody finishes", 0.8), ("of all time", 0.8), ("i would rather", 0.7), ("i'd rather", 0.7),
        ("way too", 0.6), ("honestly", 0.4),
    ],
    "win": [
        ("clutch", 1.3), ("world record", 1.2), ("headshot", 1.1), ("i got him", 1.1), ("first try", 1.1),
        ("one enemy left", 1.0), ("one hp", 1.0), ("no scope", 1.0), ("what a shot", 1.0), ("i won", 1.0),
        ("we won", 1.0), ("got him", 0.9), ("one health", 0.9), ("victory", 0.9), ("got them", 0.8), ("one shot", 0.8),
        ("ace", 0.8), ("destroyed", 0.7), ("what a goal", 1.1), ("goal", 0.6), ("gg", 0.6), ("speedrun", 0.6),
    ],
    "fail": [
        ("lost everything", 1.3), ("worst fail", 1.2), ("progress gone", 1.2), ("fell off", 1.0), ("i fell", 1.0),
        ("i died", 1.0), ("i messed up", 1.0), ("how did i miss", 1.0), ("i'm dead", 0.9), ("i am dead", 0.9),
        ("i lost", 0.9), ("we lost", 0.9), ("game over", 0.9), ("screwed up", 0.9), ("killed me", 0.9), ("fail", 0.9),
        ("wasted", 0.8), ("busted", 0.7), ("missed", 0.5), ("my bad", 0.5), ("gone", 0.4),
    ],
    "rage": [
        ("are you kidding me", 1.2), ("no no no", 1.1), ("i'm done", 1.1), ("i am done", 1.1), ("uninstall", 1.1),
        ("i quit", 1.0), ("hate this", 1.0), ("rigged", 1.0), ("cheater", 0.9), ("hacker", 0.9), ("so annoying", 0.8),
        ("trash", 0.7), ("garbage", 0.7), ("so bad", 0.6),
    ],
    "emotion": [
        ("heartbroken", 1.1), ("terrified", 1.0), ("i cried", 1.0), ("crying", 0.9), ("so happy", 0.9),
        ("means a lot", 0.9), ("shaking", 0.9), ("emotional", 0.9), ("scared", 0.8), ("proud", 0.8), ("i hate", 0.8),
        ("i love", 0.7), ("angry", 0.7), ("nervous", 0.6), ("i'm sorry", 0.6), ("mad", 0.5),
    ],
    # Things streamers say that are never the clip: thanking followers, fixing settings, signing off.
    "housekeeping": [
        ("thanks for the follow", 1.2), ("thank you for the follow", 1.2), ("thanks for the sub", 1.2),
        ("thank you for the sub", 1.2), ("thanks for the donation", 1.2), ("don't forget to subscribe", 1.2),
        ("like and subscribe", 1.2), ("smash that like", 1.2), ("sponsored by", 1.2), ("be right back", 1.2),
        ("thanks for watching", 1.0), ("welcome back to the stream", 1.0), ("welcome to the stream", 1.0),
        ("audio settings", 1.0), ("settings menu", 1.0), ("read some chat", 1.0), ("see you tomorrow", 1.0),
        ("that is it for today", 1.0), ("that's it for today", 1.0), ("use code", 1.0),
        ("link in the description", 1.0), ("brb", 1.0), ("give me a second", 0.9), ("give me a sec", 0.9),
        ("hello to everyone", 0.9), ("chat messages", 0.8), ("can you hear me", 0.8), ("is my mic", 0.8),
        ("let me check", 0.6), ("pick up the loot", 0.3),
    ],
    "context": [
        ("remember when i said", 1.0), ("like i mentioned", 1.0), ("as i mentioned", 1.0), ("as we discussed", 1.0),
        ("like i said", 1.0), ("as i said", 1.0), ("i told you", 1.0), ("going back to", 1.0), ("back to what", 1.0),
        ("earlier", 1.0), ("again", 0.5),
    ],
}
CATEGORY_CAP = 3.0
_PHRASES = {cat: sorted(((f" {p} ", w) for p, w in items), key=lambda x: -len(x[0])) for cat, items in CUES.items()}

STRICT_FILLERS = {"um", "uh", "uhm", "umm", "er", "erm", "ah", "hmm", "mm", "mhm"}
SOFT_FILLERS = {"so", "okay", "ok", "alright", "right", "well", "anyway", "anyways", "yeah", "yep", "like", "and",
                "but", "now", "basically", "oh"}
WEAK_OPENERS = STRICT_FILLERS | {"anyway", "anyways", "basically"}
DEPENDENT_FIRST = {"and", "but", "or", "because", "cause", "which", "then", "also", "plus"}
DEPENDENT_PHRASES = ("that's why", "that is why", "which is why", "like i said", "as i said", "as i was saying",
                     "he said", "she said", "they said", "the other thing", "another thing", "same thing",
                     "speaking of", "on that note", "back to")
# "No, you are wrong" / "Yeah, but ..." answer someone else, so they lean on what came before.
# "No way!" and "No! I fell!" are exclamations, not replies, and do not match.
_REPLY = re.compile(r"^(no|nope|yes|yeah|yep|right|true|sure|exactly)\b[,.]?\s+"
                    r"(you|you're|that|that's|it|it's|but|i|we|they|he|she|because)\b", re.I)
PRONOUN_FIRST = {"he", "she", "they", "it", "that", "those", "these", "him", "her", "them", "his", "their"}
CONNECTOR_LAST = {"and", "but", "so", "because", "or", "then", "like", "the", "a", "to", "of", "with"}
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
                "thousand", "million", "billion", "first", "second", "third", "half", "double", "triple", "zero"}
SECOND_PERSON = {"you", "your", "you're", "yours", "yourself", "you've", "you'll"}
QUESTION_WORDS = {"what", "why", "how", "who", "where", "when", "which", "whose"}
QUESTION_AUX = {"can", "could", "would", "should", "will", "do", "does", "did", "is", "are", "was", "were",
                "have", "has", "had", "am"}
QUESTION_SUBJECTS = {"i", "you", "we", "they", "he", "she", "it", "this", "that", "there", "anyone", "someone",
                     "anybody", "somebody", "people"}
STOPWORDS = {
    "a", "about", "above", "after", "again", "all", "also", "am", "an", "and", "any", "are", "aren't", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "can't", "could",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "even", "ever", "few",
    "for", "from", "further", "get", "got", "had", "has", "have", "having", "he", "he's", "her", "here", "hers",
    "him", "his", "how", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its",
    "just", "let", "let's", "like", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "oh", "ok",
    "okay", "on", "once", "only", "or", "other", "our", "out", "over", "own", "really", "same", "she", "she's",
    "so", "some", "such", "than", "that", "that's", "the", "their", "them", "then", "there", "there's", "these",
    "they", "they're", "this", "those", "through", "to", "too", "uh", "um", "under", "until", "up", "very", "was",
    "wasn't", "we", "we're", "well", "were", "what", "what's", "when", "where", "which", "while", "who", "why",
    "will", "with", "won't", "would", "yeah", "you", "you're", "your", "yours", "going", "gonna", "thing",
    "things", "right", "know", "think", "one", "go", "say", "said",
}


def normalize(text: str) -> str:
    """Lower-case, drop punctuation (apostrophes stay), single spaces, padded so ' phrase ' matches whole words."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    return " " + _WS.sub(" ", _PUNCT.sub(" ", text)).strip() + " "


def _clean(token: str) -> str:
    return token.lower().replace("’", "'").strip(_STRIP)


def content_tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def leading_filler_count(tokens: list[str]) -> int:
    """How many words at the start are pure filler and can be skipped without changing the meaning.

    "um"/"uh" always count. Softer words ("so", "okay", "well", "and"...) only count when they are followed by
    a pause mark or by more filler, so "Well done" and "So what happened" are left alone.
    """
    n = len(tokens)
    k = 0
    while k < n:
        raw = tokens[k]
        word = _clean(raw)
        punctuated = raw.rstrip("\"')]”’")[-1:] in ",.;:!?…-"
        following = _clean(tokens[k + 1]) if k + 1 < n else ""
        if word in STRICT_FILLERS or (word in SOFT_FILLERS and (
                punctuated or following in STRICT_FILLERS or following in SOFT_FILLERS)):
            k += 1
            continue
        break
    return k if n - k >= 2 else 0


@dataclass(slots=True)
class TextCues:
    hook: float = 0.0
    humor: float = 0.0
    excitement: float = 0.0
    surprise: float = 0.0
    conflict: float = 0.0
    story: float = 0.0
    hot_take: float = 0.0
    win: float = 0.0
    fail: float = 0.0
    rage: float = 0.0
    emotion: float = 0.0
    housekeeping: float = 0.0
    context_refs: int = 0
    question: bool = False
    exclaim: bool = False
    filler_start: bool = False
    dependent_start: bool = False
    pronoun_start: bool = False
    connector_end: bool = False
    numbers: int = 0
    second_person: int = 0
    laugh: int = 0
    n_tokens: int = 0
    hits: dict[str, list[str]] = field(default_factory=dict)


def analyze_text(text: str) -> TextCues:
    norm = normalize(text)
    tokens = norm.split()
    cues = TextCues(n_tokens=len(tokens))
    for category, phrases in _PHRASES.items():
        work = norm
        total = 0.0
        found: list[str] = []
        for phrase, weight in phrases:  # longest first; a matched span cannot be matched again by a shorter cue
            count = work.count(phrase)
            if count:
                total += weight * min(count, 2)
                found.append(phrase.strip())
                work = work.replace(phrase, " # ")
        if not found:
            continue
        cues.hits[category] = found
        if category == "context":
            cues.context_refs = len(found)
        else:
            setattr(cues, category, min(total, CATEGORY_CAP))

    stripped = text.strip().rstrip("\"')]”’")
    cues.question = stripped.endswith("?")
    if not cues.question and stripped[-1:] not in ".!…" and tokens:
        # No punctuation at all (the model skipped it): fall back on how the sentence is phrased.
        cues.question = tokens[0] in QUESTION_WORDS or (
            len(tokens) > 1 and tokens[0] in QUESTION_AUX and tokens[1] in QUESTION_SUBJECTS)
    cues.exclaim = "!" in text
    cues.laugh = len(_LAUGH.findall(text))
    cues.numbers = sum(1 for t in tokens if t in NUMBER_WORDS or any(ch.isdigit() for ch in t))
    cues.second_person = sum(1 for t in tokens if t in SECOND_PERSON)
    if tokens:
        first = tokens[0]
        second = tokens[1] if len(tokens) > 1 else ""
        raw_first = text.split()[0] if text.split() else ""
        soft_filler = first in SOFT_FILLERS and first not in DEPENDENT_FIRST and (
            len(tokens) == 1 or second in STRICT_FILLERS or second in SOFT_FILLERS
            or raw_first.rstrip("\"')]”’")[-1:] in ",.;:!?…-")
        cues.filler_start = first in WEAK_OPENERS or soft_filler
        cues.dependent_start = (first in DEPENDENT_FIRST or any(norm.startswith(f" {p} ") for p in DEPENDENT_PHRASES)
                                or _REPLY.match(text.strip().replace("’", "'")) is not None)
        cues.pronoun_start = first in PRONOUN_FIRST
        cues.connector_end = tokens[-1] in CONNECTOR_LAST and not cues.question
    return cues
