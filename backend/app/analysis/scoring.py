"""The explainable scorer: 12 sub-scores (0-100) per candidate, a weighted total, a confidence value
and the evidence behind them.

Design rules, straight from the product brief:
  * Signals help, they do not decide. Loudness can only lift parts of two sub-scores and is capped, so a
    shouted "um, let me check my settings" never outranks a story with a hook and a punchline.
  * Structure matters most: does the clip open on a hook, stand on its own, and end right after the payoff.
  * Every number is traceable. With explain=True each adjustment is recorded with the line or the
    timestamp that caused it.
These are heuristic estimates of engagement potential, not predictions of views.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..transcription.sentences import Sentence, Word
from .audio import AudioFeatures, _smooth
from .candidates import WindowOptions, refine_bounds
from .feedback import FeedbackProfile
from .lexicon import TextCues, analyze_text, content_tokens, normalize
from .presets import CONTENT_PRESETS, DIMENSIONS, GAME_STYLES, effective_weights
from .visual import DARK_LEVEL, STATIC_MOTION, VisualFeatures

LONG_GAP = 0.35        # a gap between words longer than this counts as a pause
RUN_INTENSITY = 1.2    # sentences at least this intense belong to the payoff "run"
TOPIC_BREAK_GAP = 1.5  # a pause this long before a calm line usually means the subject changed
# Cues and signals alone never report "high" confidence (0.75+); that needs a language-model pass.
HEURISTIC_CONFIDENCE_CAP = 0.74
CUE_SUMS = ("hook", "humor", "excitement", "surprise", "conflict", "story", "hot_take", "win", "fail", "rage",
            "emotion", "housekeeping")
CATEGORY_LABELS = {"funny": "Funny moment", "hype": "Hype / clutch", "fail": "Fail", "rage": "Rage",
                   "hot_take": "Hot take", "debate": "Debate", "story": "Story", "surprise": "Surprise",
                   "reaction": "Reaction", "highlight": "Highlight", "action": "Gameplay action"}


@dataclass
class ScoreOptions:
    preset: str = "auto"
    weights: dict[str, float] | None = None
    feedback: FeedbackProfile | None = None
    niche_keywords: tuple[str, ...] = ()
    game_style: str = ""
    language: str = "en"
    pre_roll: float = 0.15
    post_roll: float = 0.35


@dataclass
class Scored:
    i: int
    j: int
    start: float
    end: float
    first_word: int
    score: float
    subscores: dict[str, float]
    confidence: float
    category: str
    evidence: list[dict[str, Any]]
    explanation: str
    peak_sentence: int
    tokens: frozenset[str]
    last_word: int = 0
    peak_time: float = 0.0


def _clip(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _prefix(values) -> list[float]:  # noqa: ANN001
    out = [0.0]
    total = 0.0
    for v in values:
        total += float(v)
        out.append(total)
    return out


def _quote(text: str, max_words: int = 12) -> str:
    parts = text.split()
    return text if len(parts) <= max_words else " ".join(parts[:max_words]) + "…"


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class ScoringContext:
    """Pre-computes everything per sentence once, so scoring one candidate window is cheap."""

    def __init__(self, words: list[Word], sentences: list[Sentence], audio: AudioFeatures | None,
                 visual: VisualFeatures | None, options: ScoreOptions | None = None,
                 duration: float | None = None) -> None:
        self.words, self.sentences, self.audio, self.visual = words, sentences, audio, visual
        self.options = options or ScoreOptions()
        self.weights = self.options.weights or effective_weights(self.options.preset, self.options.feedback)
        self.duration = duration or max(audio.duration if audio else 0.0, visual.duration if visual else 0.0,
                                        words[-1].end if words else 0.0)
        self._open_cache: dict[tuple[int, int, int], tuple[TextCues, str, int, bool]] = {}
        self._prepare_words()
        self._prepare_audio()
        self._prepare_sentences()
        self._prepare_visual()

    # ------------------------------------------------------------------------------------ preparation
    def _prepare_words(self) -> None:
        words = self.words
        n = len(words)
        self.w_start = [w.start for w in words]
        self.w_end = [w.end for w in words]
        is_token = [1.0 if w.text.strip() else 0.0 for w in words]
        self.c_tok = _prefix(is_token)
        self.c_prob = _prefix(w.prob * t for w, t in zip(words, is_token))
        self.c_low = _prefix(1.0 if (t and w.prob < 0.5) else 0.0 for w, t in zip(words, is_token))
        self.c_long = _prefix(1.0 if len(w.text.strip(".,!?\"'")) >= 14 else 0.0 for w in words)
        self.gaps = [max(0.0, words[k + 1].start - words[k].end) for k in range(n - 1)] + [9.0]
        self.c_gap = _prefix(g if g > LONG_GAP else 0.0 for g in self.gaps[:-1])

    def _prepare_audio(self) -> None:
        audio = self.audio
        if audio is None or audio.rms_db.size == 0:
            self._loud = None
            return
        active = audio.rms_db > audio.silence_db
        self._loud = np.where(active, _smooth(audio.delta_db), -40.0)
        self._level = np.where(active, audio.rms_db, np.nan)

    def _loud_peak(self, start: float, end: float) -> tuple[float, float]:
        """(dB above the local baseline, time of that peak) inside a time range."""
        if self._loud is None:
            return 0.0, start
        hop = self.audio.hop
        a, b = max(0, int(start / hop)), min(self._loud.size, int(math.ceil(end / hop)))
        if b <= a:
            return 0.0, start
        k = int(np.argmax(self._loud[a:b]))
        return max(0.0, float(self._loud[a + k])), (a + k + 0.5) * hop

    def _prepare_sentences(self) -> None:
        sentences, words = self.sentences, self.words
        n = len(sentences)
        self.cues: list[TextCues] = [analyze_text(s.text) for s in sentences]
        self.n_words = [int(self.c_tok[s.w1] - self.c_tok[s.w0]) for s in sentences]
        self.gap_before = [(s.start - words[s.w0 - 1].end) if s.w0 > 0 else 9.0 for s in sentences]
        self.gap_after = [(words[s.w1].start - s.end) if s.w1 < len(words) else 9.0 for s in sentences]

        self.loud_peak, self.loud_time, self.lvl_lo, self.lvl_hi = [], [], [], []
        for s in sentences:
            peak, when = self._loud_peak(s.start, s.end + 0.3)
            self.loud_peak.append(peak)
            self.loud_time.append(when)
            lo, hi = math.nan, math.nan
            if self._loud is not None:
                hop = self.audio.hop
                seg = self._level[max(0, int(s.start / hop)):int(math.ceil(s.end / hop))]
                seg = seg[~np.isnan(seg)]
                if seg.size:
                    lo, hi = (float(v) for v in np.percentile(seg, [20, 95]))
            self.lvl_lo.append(lo)
            self.lvl_hi.append(hi)

        self.text_strength, self.loud_strength, self.intensity, self.quotable = [], [], [], []
        for k, c in enumerate(self.cues):
            text = min(3.5, 1.2 * c.humor + c.excitement + c.surprise + 0.9 * c.conflict + c.rage
                       + 0.6 * (c.win + c.fail) + 0.8 * c.hot_take + (0.5 if c.exclaim else 0.0) + min(2, c.laugh))
            loud = _clip(self.loud_peak[k] / 8.0, 0.0, 1.5)
            self.text_strength.append(text)
            self.loud_strength.append(loud)
            self.intensity.append(text + loud)
            self.quotable.append(4 <= self.n_words[k] <= 12 and not c.question and (
                c.hot_take > 0 or c.humor > 0 or c.surprise > 0 or c.conflict > 0 or c.exclaim
                or c.win + c.fail >= 0.9))

        # A topic break: a real pause followed by a line that is not a reaction. (A pause followed by a
        # shout is the opposite: the beat before a payoff.)
        self.break_before = [k > 0 and self.gap_before[k] >= TOPIC_BREAK_GAP and self.intensity[k] < RUN_INTENSITY
                             for k in range(n)]

        self._c: dict[str, list[float]] = {name: _prefix(getattr(c, name) for c in self.cues) for name in CUE_SUMS}
        self._c["break"] = _prefix(1.0 if b else 0.0 for b in self.break_before)
        for name in ("context_refs", "exclaim", "question", "laugh", "second_person", "numbers"):
            self._c[name] = _prefix(float(getattr(c, name)) for c in self.cues)
        self._c["short"] = _prefix(1.0 if nw <= 6 else 0.0 for nw in self.n_words)
        self._c["quotable"] = _prefix(1.0 if q else 0.0 for q in self.quotable)

        # vocabulary: how distinctive is each sentence compared with the rest of the video
        self.sent_tokens = [frozenset(content_tokens(s.text)) for s in sentences]
        df: Counter[str] = Counter()
        for toks in self.sent_tokens:
            df.update(toks)
        idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        idf_sums = [sum(idf[t] for t in toks) for toks in self.sent_tokens]
        self._c["idf"] = _prefix(idf_sums)
        self._c["content"] = _prefix(len(toks) for toks in self.sent_tokens)
        means = [s / len(t) for s, t in zip(idf_sums, self.sent_tokens) if t]
        self.idf_p10, self.idf_p90 = ((float(v) for v in np.percentile(means, [10, 90])) if len(means) >= 8
                                      else (0.0, 0.0))

        # audience relevance: preset vocabulary, game vocabulary and the user's own niche words
        preset = CONTENT_PRESETS.get(self.options.preset, CONTENT_PRESETS["auto"])
        game = GAME_STYLES.get(self.options.game_style, {}).get("words", [])
        self._c["kw"] = _prefix(self._keyword_hits(s.text, preset["keywords"]) + 1.5 * self._keyword_hits(s.text, game)
                                for s in sentences)
        self._c["niche"] = _prefix(self._keyword_hits(s.text, self.options.niche_keywords) for s in sentences)

    @staticmethod
    def _keyword_hits(text: str, keywords) -> float:  # noqa: ANN001
        if not keywords:
            return 0.0
        norm = normalize(text)
        return float(sum(1 for kw in keywords if f" {kw.lower()} " in norm))

    def _prepare_visual(self) -> None:
        v = self.visual
        if v is None or v.motion.size == 0:
            self._vis = None
            return
        self._vis = (_prefix(v.motion), _prefix(v.brightness < DARK_LEVEL), _prefix(v.motion < STATIC_MOTION))

    def _visual_stats(self, start: float, end: float) -> dict[str, float] | None:
        if self._vis is None:
            return None
        v = self.visual
        a = max(0, int(math.ceil(start * v.fps - 1e-9)))
        b = min(v.motion.size, int(math.floor(end * v.fps + 1e-9)) + 1)
        if b <= a:
            return None
        n = b - a
        motion, dark, static = self._vis
        mean = (motion[b] - motion[a]) / n
        return {"activity": min(1.0, mean / max(v.motion_ref, 0.02)), "dark": (dark[b] - dark[a]) / n,
                "static": (static[b] - static[a]) / n,
                "cuts": bisect.bisect_left(v.cuts, end) - bisect.bisect_left(v.cuts, start)}

    # ----------------------------------------------------------------------------------------- scoring
    def _opening(self, i: int, j: int, first: int) -> tuple[TextCues, str, int, bool]:
        """Cues of the opening line(s): the first sentence after filler trimming, extended to the next
        sentence when it is only a word or two ("Wait.")."""
        key = (i, first, min(j, i + 2))
        cached = self._open_cache.get(key)
        if cached is not None:
            return cached
        sentences, words = self.sentences, self.words
        tokens = [words[k].text.strip() for k in range(first, sentences[i].w1) if words[k].text.strip()]
        first_len = len(tokens)
        question = self.cues[i].question
        k = i
        while len(tokens) < 4 and k < j:
            k += 1
            tokens += sentences[k].text.split()
            question = question or self.cues[k].question
        text = " ".join(tokens)
        result = (analyze_text(text), text, first_len, question)
        self._open_cache[key] = result
        return result

    def score(self, i: int, j: int, *, explain: bool = False) -> Scored:
        o = self.options
        sentences, words, cues, w = self.sentences, self.words, self.cues, self.weights
        bounds = refine_bounds(i, j, words, sentences, WindowOptions(0.0, 1e9, o.pre_roll, o.post_roll), self.duration)
        start, end, f, last_w = bounds.start, bounds.end, bounds.first_word, bounds.last_word
        length = max(end - start, 0.5)
        per10 = length / 10.0
        # Cue density for entertainment and emotion is softened, so a quiet setup before a payoff does not
        # count against the clip the way plain "cues per second" would.
        per10_soft = per10 ** 0.6
        n_sent = j - i + 1
        ev: list[dict[str, Any]] = []

        def add(dimension: str, impact: float, text: str, time: float | None = None, quote: str | None = None) -> None:
            if explain and abs(impact) >= 1.0:
                ev.append({"dimension": dimension, "impact": round(impact, 1), "text": text,
                           "time": None if time is None else round(time, 2), "quote": quote})

        def total(name: str) -> float:
            return self._c[name][j + 1] - self._c[name][i]

        n_tok = int(self.c_tok[last_w + 1] - self.c_tok[f])
        mean_conf = (self.c_prob[last_w + 1] - self.c_prob[f]) / max(n_tok, 1)
        silence = self.c_gap[last_w] - self.c_gap[f]
        longest_gap = max(self.gaps[f:last_w], default=0.0)
        speech_time = max(length - silence, 0.5)
        rate, spoken_rate = n_tok / length, n_tok / speech_time
        hk = total("housekeeping")
        hk_density = hk / per10
        last = cues[j]

        peak_delta, peak_delta_time = 0.0, start
        for k in range(i, j + 1):
            if self.loud_peak[k] > peak_delta:
                peak_delta, peak_delta_time = self.loud_peak[k], self.loud_time[k]
        lows = [v for v in self.lvl_lo[i:j + 1] if not math.isnan(v)]
        highs = [v for v in self.lvl_hi[i:j + 1] if not math.isnan(v)]
        dyn_range = (max(highs) - min(lows)) if lows and highs else 0.0
        vis = self._visual_stats(start, end)

        # ---- story shape: where the payoff is, and whether the clip holds one complete moment ---------
        seg = self.intensity[i:j + 1]
        peak = i + max(range(n_sent), key=lambda k: (seg[k], k))
        run = peak  # first sentence of the unbroken reaction that ends in the peak
        while run > i and self.intensity[run - 1] >= RUN_INTENSITY:
            run -= 1
        # starts on the reaction although its lead-up is right before the clip
        no_setup = (run == i and i > 0 and self.intensity[i] >= RUN_INTENSITY and self.gap_before[i] < 3.0
                    and not self.break_before[i])
        # stops while the reaction is still going
        cut_off = (j + 1 < len(sentences) and self.intensity[j + 1] >= RUN_INTENSITY and self.gap_after[j] < 1.0)
        breaks = ([k for k in range(i + 1, j + 1) if self.break_before[k]]
                  if self._c["break"][j + 1] - self._c["break"][i + 1] > 0 else [])
        stale_lead = runs_on = False
        if breaks:
            lead = sentences[breaks[0]].start - start
            trail = end - sentences[breaks[-1]].start
            stale_lead = lead < max(6.0, 0.3 * length)          # opens on the tail of the previous subject
            runs_on = not stale_lead and trail < max(5.0, 0.2 * length) and peak < breaks[-1]

        # ---- category -----------------------------------------------------------------------------
        sums = {name: total(name) for name in CUE_SUMS}
        laughs = total("laugh")
        category_scores = {
            "funny": 1.3 * sums["humor"] + laughs, "hype": sums["excitement"] + sums["win"],
            "fail": 1.2 * sums["fail"], "rage": 1.1 * sums["rage"],
            "hot_take": 1.2 * sums["hot_take"] + 0.3 * sums["conflict"], "debate": 1.1 * sums["conflict"],
            "story": 0.8 * sums["story"], "surprise": sums["surprise"],
        }
        category = max(category_scores, key=category_scores.get)
        if category_scores[category] < 0.8:
            category = "reaction" if peak_delta >= 8 else "highlight"

        # ---- 1. opening hook ----------------------------------------------------------------------
        oc, open_text, first_len, open_question = self._opening(i, j, f)
        lead_gap = self.gap_before[i]  # the pause before this sentence (filler trimmed off the front aside)
        early_words = min(bisect.bisect_left(self.w_start, start + 3.0), last_w + 1) - f
        open_loud, _ = self._loud_peak(start, min(end, start + 3.0))
        cue_points = min(26.0, 9 * oc.hook + 6 * oc.surprise + 5 * oc.conflict + 5 * oc.hot_take + 4 * oc.excitement
                         + 3 * oc.story + 3 * oc.humor + 3 * (oc.win + oc.fail))
        loud_points = _clip((open_loud - 2.0) * 1.5, 0.0, 12.0)
        early_points = 8 if early_words >= 7 else 4 if early_words >= 5 else -10 if early_words <= 2 else 0
        hook = (30 + cue_points + (10 if open_question else 0) + (5 if oc.exclaim else 0) + (3 if oc.numbers else 0)
                + min(5.0, 2.5 * oc.second_person) + loud_points
                + (8 if lead_gap >= 0.6 else 4 if lead_gap >= 0.3 else 0) + early_points
                - (22 if oc.filler_start else 0) - (18 if oc.dependent_start else 0)
                - min(15.0, 10 * oc.housekeeping) - min(8.0, max(0, first_len - 26) * 0.6)
                - (8 if stale_lead else 0) - (6 if no_setup else 0))
        if explain:
            opener = _quote(open_text)
            if stale_lead:
                add("hook", -8, "Opens on the tail end of the previous subject", start, opener)
            if open_question:
                add("hook", 10 + cue_points, "Opens with a question", start, opener)
            elif cue_points >= 8:
                phrase = next((p for cat in ("hook", "hot_take", "surprise", "conflict", "excitement", "win", "fail")
                               for p in oc.hits.get(cat, [])), "")
                add("hook", cue_points, f"Opens on a strong line (“{phrase}”)" if phrase else "Opens on a strong line",
                    start, opener)
            if loud_points >= 4:
                add("hook", loud_points, f"Energetic from the first seconds ({open_loud:.0f} dB above the "
                                         f"surrounding level)", start)
            if oc.filler_start:
                add("hook", -22, "Starts on filler words", start, opener)
            if oc.dependent_start:
                add("hook", -18, "The first line leans on something said earlier", start, opener)
            if early_points < 0:
                add("hook", early_points, "Slow start: almost nothing is said in the first 3 seconds", start)
            if oc.housekeeping:
                add("hook", -min(15.0, 10 * oc.housekeeping), "Opens on stream housekeeping", start, opener)

        # ---- 2. entertainment ---------------------------------------------------------------------
        fun = (1.2 * sums["humor"] + sums["excitement"] + 0.9 * sums["surprise"] + 0.9 * sums["conflict"]
               + 0.8 * (sums["win"] + sums["fail"]) + 0.8 * sums["rage"] + 0.5 * sums["story"]
               + 0.7 * sums["hot_take"] + 1.5 * laughs)
        fun_points = 52 * (1 - math.exp(-(fun / per10_soft) / 2.2))
        audible = _clip((peak_delta - 4.0) * 1.5, 0.0, 12.0)
        short_ratio = total("short") / n_sent if n_sent >= 4 else 0.0
        activity = vis["activity"] if vis else 0.5
        entertainment = (18 + fun_points + audible + min(8.0, 16 * short_ratio) + 10 * activity
                         - min(25.0, 12 * hk_density))
        if explain:
            if fun_points >= 12:
                found = [p for cat in ("humor", "excitement", "surprise", "conflict", "win", "fail", "rage", "hot_take")
                         for k in range(i, j + 1) for p in cues[k].hits.get(cat, [])]
                shown = ", ".join(f"“{p}”" for p in list(dict.fromkeys(found))[:3])
                add("entertainment", fun_points, f"{len(found)} reaction, humour or conflict cues ({shown})")
            if hk_density > 0.3:
                add("entertainment", -min(25.0, 12 * hk_density),
                    "Mostly stream housekeeping (thanking followers, settings, reading chat)")

        # ---- 3. emotional impact ------------------------------------------------------------------
        exclaims = total("exclaim")
        feeling = (sums["excitement"] + sums["rage"] + sums["surprise"] + 0.8 * sums["humor"] + sums["emotion"]
                   + 0.6 * sums["conflict"] + 0.5 * exclaims)
        feeling_points = 45 * (1 - math.exp(-(feeling / per10_soft) / 2.0))
        voice_points = _clip((dyn_range - 6.0) * 1.2, 0.0, 16.0) + _clip((peak_delta - 4.0) * 1.5, 0.0, 14.0)
        emotion = 15 + feeling_points + voice_points + min(10.0, 3 * exclaims)
        if explain and peak_delta >= 6:
            add("emotion", voice_points, f"Voice rises {peak_delta:.0f} dB above the surrounding minutes at "
                                         f"{_clock(peak_delta_time)}", peak_delta_time)

        # ---- 4. clear without outside context -----------------------------------------------------
        refs = total("context_refs")
        question_inside = any(cues[k].question for k in range(i, j))
        conf_penalty = _clip((0.86 - mean_conf) * 120, 0.0, 26.0)
        clarity = (72 - (24 if oc.dependent_start else 0) - min(18.0, 7 * refs)
                   - (10 if oc.pronoun_start and not oc.dependent_start else 0)
                   + (8 if lead_gap >= 0.6 else 0) + (6 if question_inside else 0) + (6 if oc.story > 0 else 0)
                   - conf_penalty - (18 if n_tok < 12 else 8 if n_tok < 20 else 0) - (8 if last.question else 0)
                   - (16 if stale_lead else 0) - (14 if no_setup else 0) - (8 if len(breaks) >= 3 else 0))
        if explain:
            if stale_lead:
                add("clarity", -16, "Mixes two subjects: the first lines belong to what came before",
                    sentences[breaks[0]].start)
            if no_setup:
                add("clarity", -14, "Starts on the reaction; the lead-up that explains it is just before the clip",
                    start)
            if len(breaks) >= 3:
                add("clarity", -8, "Jumps between several separate bits")
            if oc.dependent_start:
                add("clarity", -24, "Needs earlier context to make sense", start, _quote(open_text))
            elif lead_gap >= 0.6 and refs == 0 and not stale_lead and not no_setup:
                add("clarity", 8, "Stands on its own: it starts after a natural break and refers to nothing earlier",
                    start)
            if refs:
                phrase = next((p for k in range(i, j + 1) for p in cues[k].hits.get("context", [])), "")
                add("clarity", -min(18.0, 7 * refs), f"Refers back to something said earlier (“{phrase}”)")
            if n_tok < 12:
                add("clarity", -18, f"Very little is said ({n_tok} words)")

        # ---- 5. visual interest -------------------------------------------------------------------
        if vis is None:
            visual = 50.0
        else:
            visual = (34 + 46 * vis["activity"] + (6 if 1 <= vis["cuts"] <= 3 else 0) - (10 if vis["cuts"] >= 8 else 0)
                      - 30 * vis["dark"] - (12 if vis["static"] > 0.9 else 0))
            if explain:
                if vis["activity"] >= 0.75:
                    add("visual", 46 * vis["activity"] - 23, "The picture is among the busiest parts of the video")
                if vis["static"] > 0.9:
                    add("visual", -12, "The picture barely changes")
                if vis["dark"] > 0.3:
                    add("visual", -30 * vis["dark"], f"{vis['dark'] * 100:.0f}% of the frames are nearly black")

        # ---- 6. pacing ----------------------------------------------------------------------------
        if 2.3 <= rate <= 3.9:
            rate_points = 92.0
        elif rate < 2.3:
            rate_points = 92.0 - (2.3 - rate) * 38
        else:
            rate_points = 92.0 - (rate - 3.9) * 22
        silence_ratio = silence / length
        gap_penalty = _clip((longest_gap - 1.8) * 9, 0.0, 28.0)
        pacing = rate_points - _clip((silence_ratio - 0.22) * 90, 0.0, 35.0) - gap_penalty
        if explain:
            if gap_penalty > 0:
                add("pacing", -gap_penalty, f"Contains {longest_gap:.1f} s of dead air")
            elif rate_points >= 90 and silence_ratio < 0.2:
                add("pacing", 12, f"Steady pace ({rate:.1f} words per second, no dead air)")
            elif rate < 1.6:
                add("pacing", rate_points - 92, f"Slow ({rate:.1f} words per second)")

        # ---- 7. payoff ----------------------------------------------------------------------------
        # A real payoff is something said (a punchline, a reaction with words). Loudness alone adds little,
        # and the structure bonuses only count in full when there is a payoff to place.
        text_s = max(self.text_strength[run:peak + 1])
        loud_s = self.loud_strength[peak]
        position = (sentences[peak].end - start) / length
        tail = end - sentences[peak].end
        end_gap = self.gaps[last_w]
        factor = _clip(text_s / 1.2, 0.25, 1.0)
        place = 12 if position >= 0.55 else 6 if position >= 0.4 else -6 if position < 0.25 else 0
        tail_points = 9 if tail <= 4 else 3 if tail <= 8 else -12 if tail > 14 else -4
        stop_points = 7 if end_gap >= 0.5 else 3 if end_gap >= 0.25 else -6
        finish = (4 if (last.exclaim or last.laugh) else 0) + (3 if self.n_words[j] <= 9 and not last.question else 0)
        structure = sum(p * factor if p > 0 else p for p in (place, tail_points, stop_points, finish))
        pause_first = (run > i and 0.7 <= self.gap_before[run] <= 2.5
                       and (self.loud_peak[run] >= 6 or self.intensity[run] >= 1.5))
        k40 = max(1, round(0.4 * n_sent))
        rising = n_sent >= 3 and (sum(seg[-k40:]) / k40) > (sum(seg[:k40]) / k40) + 0.3
        payoff = (24 + _clip(text_s * 9, 0.0, 20.0) + _clip(loud_s * 3, 0.0, 4.0)
                  + (4 if text_s >= 1 and loud_s >= 0.75 else 0) + structure
                  - (12 if last.question else 0) - (14 if last.connector_end else 0)
                  + (8 if pause_first else 0) + (5 if rising else 0) - (10 if text_s < 0.5 else 0)
                  - (12 if cut_off else 0) - (10 if runs_on else 0))
        if explain:
            line = _quote(sentences[peak].text)
            when = sentences[peak].start
            if text_s >= 1.0 and tail <= 8:
                add("payoff", _clip(text_s * 9, 0, 20) + max(0.0, tail_points) * factor,
                    f"The payoff lands at {position * 100:.0f}% of the clip, {tail:.1f} s before it ends", when, line)
            elif text_s >= 1.0:
                add("payoff", tail_points, f"The payoff comes at {position * 100:.0f}% and the clip runs on for "
                                           f"{tail:.0f} s after it", when, line)
            else:
                add("payoff", -10, "No clear punchline or reaction was found")
            if pause_first:
                add("payoff", 8, f"A {self.gap_before[run]:.1f} s pause right before the payoff",
                    sentences[run].start)
            if cut_off:
                add("payoff", -12, "Stops while the reaction is still going", sentences[j].end)
            if runs_on:
                add("payoff", -10, "Runs on into the next subject after the payoff", sentences[breaks[-1]].start)
            if last.question:
                add("payoff", -12, "Ends on a question that is not answered", sentences[j].start,
                    _quote(sentences[j].text))
            if last.connector_end:
                add("payoff", -14, "Ends mid-thought", sentences[j].start, _quote(sentences[j].text))

        # ---- 8. shareability ----------------------------------------------------------------------
        has_quote = total("quotable") > 0
        length_points = (10 if 15 <= length <= 45 else 5 if (45 < length <= 60 or 10 <= length < 15)
                         else -8 if (length > 90 or length < 8) else 0)
        shareability = (30 + (14 if has_quote else 0) + min(14.0, 7 * sums["hot_take"])
                        + min(8.0, 4 * sums["conflict"]) + min(10.0, 5 * sums["humor"])
                        + min(8.0, 4 * sums["surprise"]) + min(6.0, 2 * total("second_person")) + length_points
                        + 0.14 * (_clip(hook, 0, 100) - 50) + 0.14 * (_clip(payoff, 0, 100) - 50)
                        - min(20.0, 10 * hk_density))
        if explain and has_quote:
            best = max((k for k in range(i, j + 1) if self.quotable[k]), key=lambda k: self.text_strength[k])
            add("shareability", 14, "Has a short quotable line", sentences[best].start, _quote(sentences[best].text))

        # ---- 9. audience relevance ----------------------------------------------------------------
        niche = total("niche")
        bias = o.feedback.category_bias.get(category, 0.0) if o.feedback else 0.0
        relevance = (46 + 34 * (1 - math.exp(-(total("kw") / per10) / 1.2)) + min(15.0, 6 * niche) + bias
                     - min(18.0, 9 * hk_density))
        if explain:
            if niche:
                add("relevance", min(15.0, 6 * niche), f"Mentions {int(niche)} of your niche keywords")
            if bias:
                add("relevance", bias, f"Your feedback on “{CATEGORY_LABELS.get(category, category)}” clips")

        # ---- 10. subtitle readability -------------------------------------------------------------
        low_ratio = (self.c_low[last_w + 1] - self.c_low[f]) / max(n_tok, 1)
        long_ratio = (self.c_long[last_w + 1] - self.c_long[f]) / max(n_tok, 1)
        fast_penalty = _clip((spoken_rate - 4.0) * 16, 0.0, 30.0)
        heard_penalty = _clip(low_ratio * 140, 0.0, 40.0)
        subtitles = 96 - fast_penalty - heard_penalty - _clip(long_ratio * 120, 0.0, 12.0) - (30 if n_tok == 0 else 0)
        if explain:
            if fast_penalty >= 4:
                add("subtitles", -fast_penalty, f"Fast speech ({spoken_rate:.1f} words per second) is harder to read")
            if heard_penalty >= 4:
                add("subtitles", -heard_penalty, f"{low_ratio * 100:.0f}% of the words were hard to make out")

        # ---- 11. originality ----------------------------------------------------------------------
        content = total("content")
        if len(sentences) < 8 or content == 0 or self.idf_p90 <= self.idf_p10:
            originality = 60.0
        else:
            z = (total("idf") / content - self.idf_p10) / (self.idf_p90 - self.idf_p10)
            originality = 35 + 55 * _clip(z, 0.0, 1.0) - (15 if content / max(n_tok, 1) < 0.25 else 0)

        subscores = {
            "hook": hook, "entertainment": entertainment, "emotion": emotion, "clarity": clarity, "visual": visual,
            "pacing": pacing, "payoff": payoff, "shareability": shareability, "relevance": relevance,
            "subtitles": subtitles, "originality": originality, "uniqueness": 100.0,  # set during selection
        }
        subscores = {dim: round(_clip(value, 0.0, 100.0), 1) for dim, value in subscores.items()}
        overall = sum(w[dim] * subscores[dim] for dim in DIMENSIONS)
        if n_tok < 8:
            overall = min(overall, 38.0)
        elif mean_conf < 0.45:
            overall = min(overall, 50.0)

        # ---- confidence in this estimate ------------------------------------------------------------
        # How much the data supports the estimate (not how good the clip is): clear speech, enough words,
        # audio and picture available, and words and voice agreeing on where the payoff is.
        confidence = (0.35 + 0.18 * _clip((mean_conf - 0.55) / 0.4, 0.0, 1.0) + (0.07 if n_tok >= 25 else 0.0)
                      + (0.05 if self.audio is not None else 0.0) + (0.05 if vis is not None else 0.0)
                      + (0.06 if text_s >= 1 and peak_delta >= 5 else 0.0)
                      - (0.12 if not o.language.lower().startswith("en") else 0.0))
        confidence = round(_clip(confidence, 0.15, HEURISTIC_CONFIDENCE_CAP), 2)

        tokens: frozenset[str] = frozenset().union(*self.sent_tokens[i:j + 1])
        if explain:
            ev.sort(key=lambda item: -abs(item["impact"] * w.get(item["dimension"], 0.05)))
        return Scored(i=i, j=j, start=start, end=end, first_word=f, last_word=last_w, score=round(overall, 1),
                      subscores=subscores, confidence=confidence, category=category, evidence=ev,
                      explanation=_explain(ev) if explain else "", peak_sentence=peak,
                      tokens=tokens, peak_time=sentences[peak].start)


def _explain(evidence: list[dict[str, Any]]) -> str:
    def phrase(item: dict[str, Any]) -> str:
        return f"{item['text']}: “{item['quote']}”" if item.get("quote") else item["text"]

    good = [phrase(e) for e in evidence if e["impact"] > 0][:3]
    bad = [phrase(e) for e in evidence if e["impact"] < 0][:2]
    parts = []
    if good:
        parts.append(". ".join(good) + ".")
    if bad:
        parts.append("Held back by: " + "; ".join(b[0].lower() + b[1:] for b in bad) + ".")
    return " ".join(parts) or "No strong signals either way; an average stretch of the video."
