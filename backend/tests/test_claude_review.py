"""Claude as the engine that picks the clips. The command-line tool is never really called here: a stand-in
returns what Claude would, so these tests are fast and need no sign-in."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analysis import claude_review
from app.analysis.claude_review import ClaudeUnavailable, Pick
from app.analysis.engine import HighlightOptions, highlights_from_picks
from app.transcription.sentences import build_sentences
from tests.helpers import make_audio, make_visual, script_to_words
from tests.test_scoring import FILLER, STORY

WORDS = script_to_words(FILLER + STORY)
SENTENCES = build_sentences(WORDS)
STORY_START = next(w.start for w in WORDS if w.text == "Can")
END = WORDS[-1].end
OPTIONS = HighlightOptions(count=3, min_len=8, max_len=40)


def answer(picks: list[dict], prose: str = "") -> str:
    """What the command-line tool prints with --output-format json."""
    text = f"{prose}```json\n{json.dumps({'picks': picks})}\n```"
    return json.dumps({"type": "result", "is_error": False, "result": text})


def story_pick(**changes) -> dict:
    return {"start": STORY_START + 0.4, "end": END - 0.3, "title": "He lost his phone in his own hand",
            "hook": "45 minutes looking for my phone", "category": "funny", "score": 88,
            "reason": "A complete story with a clear punchline at the end.", **changes}


# ------------------------------------------------------------------------------------- reading the answer
def test_picks_are_read_from_the_answer_even_with_text_around_them():
    picks = claude_review.parse_answer(answer([story_pick()], prose="Here are my picks:\n"))

    assert [p.title for p in picks] == ["He lost his phone in his own hand"]
    assert picks[0].category == "funny" and picks[0].score == 88


def test_unusable_picks_are_dropped_and_values_are_kept_in_range():
    picks = claude_review.parse_answer(answer([
        story_pick(score=140, category="hilarious"),
        {"start": 50, "end": 40, "title": "backwards"},
        {"title": "no times"},
    ]))

    assert len(picks) == 1
    assert picks[0].score == 100 and picks[0].category == "highlight"


def test_claude_also_writes_the_thumbnail_words_a_tiktok_caption_and_hashtags():
    (pick,) = claude_review.parse_answer(answer([story_pick(
        thumb="he lost his phone", thumb_word="phone",
        caption="He looked for 45 minutes.\nIt was in his hand. #storytime",
        hashtags=["#GTA6", "fyp", "#lost phone", "#a", "#" + "x" * 40, "#gta6", "#2024"])]))

    assert (pick.thumb, pick.thumb_word) == ("HE LOST HIS PHONE", "PHONE")
    assert pick.caption == "He looked for 45 minutes. It was in his hand."
    assert pick.hashtags == ["#GTA6", "#fyp", "#lostphone", "#storytime"]   # cleaned, each once, not numbers alone


def test_an_answer_without_them_still_works_and_hashtags_may_come_as_one_line():
    (pick,) = claude_review.parse_answer(answer([story_pick(hashtags="#gta6 #funny, fyp")]))

    assert (pick.thumb, pick.thumb_word, pick.caption) == ("", "", "")
    assert pick.hashtags == ["#gta6", "#funny", "#fyp"]


def test_a_long_caption_is_cut_at_a_word_and_a_number_sign_stays_in_it():
    (pick,) = claude_review.parse_answer(answer([story_pick(caption="The #1 cop " + "word " * 100)]))

    assert len(pick.caption) <= 220 and pick.caption.endswith("word…") and pick.caption.startswith("The #1 cop")


@pytest.mark.parametrize(("printed", "says"), [
    (json.dumps({"is_error": True, "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}),
     "sign in"),
    (json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1764000000"}), "limit"),
    (json.dumps({"is_error": False, "result": "I could not find anything worth clipping."}), "no picks"),
    ("not json at all", "unexpected"),
])
def test_failures_are_explained(printed: str, says: str):
    with pytest.raises(ClaudeUnavailable) as failed:
        claude_review.parse_answer(printed)

    assert says in (failed.value.message + " " + failed.value.hint).lower()


# ------------------------------------------------------------------------------------- what Claude is sent
def test_the_prompt_holds_the_timed_transcript_and_the_rules():
    prompt = claude_review.build_prompt(SENTENCES, OPTIONS, title="Friday stream", creator="Kai", game="GTA",
                                        action_times=[123.5], duration=END)

    assert "Friday stream" in prompt and "Kai" in prompt and "GTA" in prompt
    assert "Can I tell you the dumbest thing" in prompt
    assert f"[{claude_review.clock(STORY_START)}]" in prompt
    assert "between 8 and 40 seconds" in prompt and "3" in prompt
    assert "2:03" in prompt                                                  # the loud, busy moment
    assert "never invent" in prompt.lower()


def test_both_prompts_ask_for_thumbnail_words_a_tiktok_caption_and_hashtags():
    prompts = [claude_review.build_prompt(SENTENCES, OPTIONS, title="Friday stream", creator="Kai", game="GTA",
                                          action_times=[], duration=END),
               claude_review.build_moment_prompt(SENTENCES, OPTIONS, start=STORY_START, end=None,
                                                 title="Friday stream", creator="Kai", game="GTA", duration=END)]

    for prompt in prompts:
        assert all(f'"{key}"' in prompt for key in ("thumb", "thumb_word", "caption", "hashtags"))
        assert "thumbnail" in prompt and "TikTok caption" in prompt


def test_the_command_runs_with_no_tools_and_outside_the_project():
    args = claude_review.command("claude", "sonnet")

    assert args[:2] == ["claude", "-p"] and "--model" in args and "sonnet" in args
    assert args[args.index("--tools") + 1] == ""
    assert "--bare" not in args                                              # that mode cannot use your plan's sign-in


# ------------------------------------------------------------------------------- picks become highlights
def run(picks: list[dict], **options):  # noqa: ANN201
    return highlights_from_picks(WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5),
                                 HighlightOptions(**{"count": 3, "min_len": 8, "max_len": 40, **options}),
                                 claude_review.parse_answer(answer(picks)), duration=END + 5)


def test_a_pick_becomes_a_clip_on_whole_sentences_with_claudes_words():
    (h,) = run([story_pick()])

    assert h.analysis_source == "claude"
    assert h.start <= STORY_START and h.end >= END - 0.05                   # widened to the whole sentences
    assert h.title == "He lost his phone in his own hand" and h.hook_text == "45 minutes looking for my phone"
    assert h.score == 88 and h.category == "funny" and h.confidence >= 0.75
    assert "Claude" in h.explanation and "punchline" in h.explanation
    assert set(h.subscores) >= {"hook", "payoff", "clarity"}                  # the 12-part breakdown is still there
    assert h.transcript_text.startswith("Can I tell you")


def test_claudes_thumbnail_words_caption_and_hashtags_go_with_the_clip():
    (h,) = run([story_pick(thumb="He lost his phone", thumb_word="phone", caption="He looked for 45 minutes.",
                           hashtags=["#lostphone", "#storytime"])])

    assert (h.extra["cover_text"], h.extra["cover_word"]) == ("HE LOST HIS PHONE", "PHONE")
    assert h.description == "He looked for 45 minutes."
    assert h.hashtags[:2] == ["#lostphone", "#storytime"] and len(h.hashtags) <= 6


def test_without_a_caption_from_claude_the_description_is_the_title_as_before():
    (h,) = run([story_pick()])

    assert h.description == "He lost his phone in his own hand" and "cover_text" not in h.extra


def test_overlapping_picks_are_merged_away_and_ranks_follow_the_score():
    second = {"start": 0.5, "end": 10.0, "title": "Settings", "category": "highlight", "score": 40, "reason": "Calm."}
    found = run([second, story_pick(), story_pick(start=STORY_START + 2, score=70, title="Same story again")])

    assert [h.title for h in found] == ["He lost his phone in his own hand", "Settings"]
    assert [h.rank for h in found] == [1, 2]


def test_too_many_picks_are_cut_to_the_number_asked_for():
    many = [{"start": t, "end": t + 9, "title": f"Pick {k}", "score": 50 + k, "reason": "x"}
            for k, t in enumerate((0.0, 12.0, 24.0, 36.0))]
    assert len(run(many, count=2)) <= 2


def test_a_pick_below_the_minimum_score_is_left_out():
    assert run([story_pick(score=40)], min_score=50) == []


# --------------------------------------------------------------------------------------------- availability
def test_the_tool_is_found_or_reported_missing(monkeypatch, tmp_path: Path):  # noqa: ANN001
    monkeypatch.setattr(claude_review.shutil, "which", lambda name: None)
    monkeypatch.setattr(claude_review, "CANDIDATE_PATHS", (tmp_path / "missing.exe",))
    assert claude_review.find_cli("") is None

    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    assert claude_review.find_cli(str(exe)) == str(exe)


def test_the_tool_gets_a_clean_environment_so_it_uses_your_own_sign_in(monkeypatch):  # noqa: ANN001
    """ClipForge may be started from inside another Claude program (the desktop app, an editor). Its settings
    must not leak into the tool, or the tool would try that program's connection instead of your sign-in."""
    for name, value in {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9/", "ANTHROPIC_API_KEY": "sk-not-yours",
                        "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_ENTRYPOINT": "desktop",
                        "CLIPFORGE_DATA_DIR": "D:/x", "PATH": "C:/Windows", "USERPROFILE": "C:/Users/me",
                        "CLAUDE_CONFIG_DIR": "C:/Users/me/.claude"}.items():
        monkeypatch.setenv(name, value)

    env = claude_review.child_env()

    for gone in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                 "CLAUDE_CODE_ENTRYPOINT", "CLIPFORGE_DATA_DIR"):
        assert gone not in env, gone
    assert env["PATH"] == "C:/Windows" and env["USERPROFILE"] == "C:/Users/me"
    assert env["CLAUDE_CONFIG_DIR"] == "C:/Users/me/.claude"          # where your sign-in is kept stays known


def test_the_sign_in_advice_names_the_exact_command():
    assert "claude auth login" in claude_review.SIGN_IN_HINT


def test_a_claude_tool_that_windows_will_not_start_counts_as_unavailable(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(claude_review, "find_cli", lambda configured="": "claude.exe")

    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise PermissionError("This program is blocked by group policy")

    monkeypatch.setattr(claude_review.subprocess, "Popen", blocked)

    with pytest.raises(ClaudeUnavailable) as failed:
        claude_review.run_claude("a transcript")
    assert "could not be started" in failed.value.message

