"""Group timed words into sentences.

Sentences are the unit clips are built from: a clip always starts at the start of a sentence and ends at
the end of one, which is what keeps cuts from landing mid-sentence.
"""
from __future__ import annotations

from dataclasses import dataclass

ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e.", "st.",
                 "mt.", "no.", "inc.", "ltd.", "u.s.", "a.m.", "p.m."}
PAUSE_SPLIT = 1.0     # a silence this long ends the sentence even without punctuation
SOFT_WORDS = 28       # past this many words, break at the next comma or breath
BREATH_GAP = 0.35
MAX_WORDS = 40        # hard limit for run-on speech with no punctuation at all
CAPITAL_GAP = 0.4           # pause before a capitalised word that starts a sentence without a full stop
UNPUNCTUATED_GAP = 0.6      # pause that ends a sentence once punctuation has stopped appearing...
UNPUNCTUATED_WORDS = 12     # ...which is assumed after this many words without a full stop
_CLOSERS = "\"')]}”’"
_I_FORMS = {"I", "I'm", "I'll", "I've", "I'd", "I’m", "I’ll", "I’ve", "I’d"}


@dataclass(slots=True)
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass(slots=True)
class Sentence:
    idx: int
    start: float
    end: float
    text: str
    w0: int  # words[w0:w1] belong to this sentence
    w1: int


def _ends_sentence(token: str) -> bool:
    core = token.rstrip(_CLOSERS)
    if not core or core[-1] not in ".?!…":
        return False
    return core.lower() not in ABBREVIATIONS


def _starts_capitalised(token: str) -> bool:
    core = token.lstrip("\"'([{“‘")
    return bool(core) and core[0].isupper() and core.rstrip(_CLOSERS + ".,!?;:") not in _I_FORMS


def build_sentences(words: list[Word]) -> list[Sentence]:
    sentences: list[Sentence] = []
    w0 = 0
    spoken = 0          # non-empty tokens in the sentence being built
    since_stop = 0      # tokens since the model last wrote a full stop, question or exclamation mark

    def emit(w1: int) -> None:
        nonlocal w0, spoken
        tokens = [(i, words[i].text.strip()) for i in range(w0, w1)]
        real = [(i, t) for i, t in tokens if t]
        if not real:
            if sentences:  # stray empty tokens join the sentence before them
                sentences[-1].w1 = w1
            w0, spoken = w1, 0
            return
        sentences.append(Sentence(idx=len(sentences), start=words[real[0][0]].start, end=words[real[-1][0]].end,
                                  text=" ".join(t for _, t in real), w0=w0, w1=w1))
        w0, spoken = w1, 0

    for i, word in enumerate(words):
        token = word.text.strip()
        if token:
            spoken += 1
            since_stop += 1
        if i == len(words) - 1:
            emit(i + 1)
            break
        gap = words[i + 1].start - word.end
        punctuated = token.rstrip(_CLOSERS)[-1:] in ",;:.?!…" if token else False
        boundary = False
        if token and _ends_sentence(token):
            boundary = True
            since_stop = 0
        elif gap >= PAUSE_SPLIT:
            boundary = True
        elif spoken >= MAX_WORDS:
            boundary = True
        elif spoken >= SOFT_WORDS and token and (token.rstrip(_CLOSERS)[-1:] in ",;:" or gap >= BREATH_GAP):
            boundary = True
        elif token and not punctuated:
            # The model does not always write punctuation. Two fallbacks, both needing a real pause:
            # the next word is capitalised (it still marks sentence starts that way), or there has been
            # no full stop for so long that the model has clearly stopped punctuating.
            if gap >= CAPITAL_GAP and spoken >= 4 and _starts_capitalised(words[i + 1].text.strip()):
                boundary = True
            elif gap >= UNPUNCTUATED_GAP and since_stop >= UNPUNCTUATED_WORDS:
                boundary = True
        if boundary:
            emit(i + 1)

    if sentences and sentences[0].w0 != 0:
        sentences[0].w0 = 0  # leading empty tokens belong to the first sentence
    return sentences
