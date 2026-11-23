"""ChatGPT (through the Codex tool) as the second opinion on the picks. The real tool is never called here:
a stand-in answers instead, so the tests are fast and need no sign-in."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analysis import chatgpt_review, claude_review
from app.analysis.chatgpt_review import ChatGPTUnavailable
from app.analysis.engine import HighlightOptions, apply_second_opinion, highlights_from_picks
from app.transcription.sentences import build_sentences
from tests.helpers import make_audio, make_visual, script_to_words
from tests.test_scoring import FILLER, STORY

WORDS = script_to_words(FILLER + FILLER + STORY)
SENTENCES = build_sentences(WORDS)
STORY_START = next(w.start for w in WORDS if w.text == "Can")
END = WORDS[-1].end
OPTIONS = HighlightOptions(count=3, min_len=8, max_len=40)


def claude_picks():  # noqa: ANN201
    picks = claude_review.parse_answer(json.dumps({"is_error": False, "result": json.dumps({"picks": [
        {"start": STORY_START + 0.3, "end": END - 0.2, "title": "Phone in his hand", "hook": "45 minutes",
         "category": "funny", "score": 88, "reason": "A full story with a punchline."},
        {"start": 0.2, "end": 9.0, "title": "Settings talk", "hook": "", "category": "highlight", "score": 55,
         "reason": "Opening chatter."}]})}))
    return highlights_from_picks(WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5), OPTIONS, picks,
                                 duration=END + 5)


def review_text(verdicts: list[dict], additions: list[dict] = (), prose: str = "") -> str:
    return prose + json.dumps({"verdicts": verdicts, "additions": list(additions)})


# ------------------------------------------------------------------------------------- reading the answer
def test_verdicts_and_additions_are_read_even_with_text_around_them():
    review = chatgpt_review.parse_answer(review_text(
        [{"pick": 1, "verdict": "agree", "reason": "Clear punchline."},
         {"pick": 2, "verdict": "disagree", "reason": "Just settings talk."}],
        [{"start": 12.0, "end": 24.0, "title": "Loot run", "category": "hype", "score": 70, "reason": "Fast."}],
        prose="Here is my review:\n"))

    assert review.verdicts == {1: ("agree", "Clear punchline."), 2: ("disagree", "Just settings talk.")}
    assert [a.title for a in review.additions] == ["Loot run"]


def test_a_moment_chatgpt_adds_comes_with_thumbnail_words_a_caption_and_hashtags():
    review = chatgpt_review.parse_answer(review_text([], [
        {"start": 12.0, "end": 24.0, "title": "Loot run", "category": "hype", "score": 70, "reason": "Fast.",
         "thumb": "full loot in 10 seconds", "thumb_word": "10", "caption": "He was not meant to get away. #gta",
         "hashtags": ["#lootrun"]}]))

    (added,) = review.additions
    assert (added.thumb, added.thumb_word) == ("FULL LOOT IN 10 SECONDS", "10")
    assert added.caption == "He was not meant to get away." and added.hashtags == ["#lootrun", "#gta"]


def test_odd_verdicts_and_broken_additions_are_ignored():
    review = chatgpt_review.parse_answer(review_text(
        [{"pick": 1, "verdict": "maybe"}, {"pick": "x", "verdict": "agree"}, {"pick": 3, "verdict": "AGREE"}],
        [{"start": 30, "end": 20, "title": "backwards"}]))

    assert review.verdicts == {3: ("agree", "")}
    assert review.additions == []


@pytest.mark.parametrize(("printed", "says"), [
    ("ERROR: unexpected status 401 Unauthorized: Missing bearer or basic authentication", "sign"),
    ("Error: Not logged in. Run codex login.", "sign"),
    ("ERROR: You've hit your usage limit. Upgrade to Pro or try again later.", "limit"),
    ("I am not sure what you want.", "unexpected"),
])
def test_failures_are_explained(printed: str, says: str):
    with pytest.raises(ChatGPTUnavailable) as failed:
        chatgpt_review.parse_answer(printed)

    assert says in (failed.value.message + " " + failed.value.hint).lower()


BANNER = ("OpenAI Codex v0.167.0\n--------\nworkdir: C:\\tmp\nmodel: gpt-6.2-astra\nprovider: openai\n--------\n"
          "user\nReply with exactly: OK\n")
REFRESH_401 = ("2026-11-23T05:57:04.796464Z ERROR codex_models_manager::manager: failed to refresh available models: "
               "unexpected status 401 Unauthorized: Your authentication token has expired.\n")
NOT_OFFERED = "ERROR: The 'gpt-6.2-astra' model is not supported when using Codex with a ChatGPT account.\n"


def test_a_model_the_chatgpt_plan_does_not_offer_is_named_as_the_reason(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-reserve", "visibility": "hide"}, {"slug": "gpt-6-luna", "visibility": "list"},
        {"slug": "gpt-5.6-terra", "visibility": "list"}]}), encoding="utf-8")

    with pytest.raises(ChatGPTUnavailable) as failed:   # an earlier token-refresh warning is not the reason
        chatgpt_review.parse_answer(BANNER + REFRESH_401 + NOT_OFFERED)

    assert "does not offer" in failed.value.message and "signed out" not in failed.value.message
    assert "Settings" in failed.value.hint and "gpt-6-luna, gpt-5.6-terra" in failed.value.hint
    assert "gpt-reserve" not in failed.value.hint


def test_without_codexs_model_list_the_advice_still_stands(tmp_path, monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))      # no models_cache.json there

    with pytest.raises(ChatGPTUnavailable) as failed:
        chatgpt_review.parse_answer(NOT_OFFERED)

    assert "does not offer" in failed.value.message and "Settings" in failed.value.hint


def test_an_unknown_failure_shows_codexs_own_error_not_its_banner():
    with pytest.raises(ChatGPTUnavailable) as failed:
        chatgpt_review.parse_answer(BANNER + "ERROR: stream disconnected before completion\n")

    assert "unexpected" in failed.value.message
    assert failed.value.hint == "ERROR: stream disconnected before completion"


# -------------------------------------------------------------------------------------- what is sent
def test_the_prompt_holds_the_transcript_and_the_numbered_picks():
    prompt = chatgpt_review.build_prompt(SENTENCES, claude_picks(), OPTIONS, title="Friday stream", creator="Kai")

    assert "Friday stream" in prompt and "Can I tell you the dumbest thing" in prompt
    assert "1." in prompt and "Phone in his hand" in prompt and "2." in prompt and "Settings talk" in prompt
    assert "agree" in prompt and "disagree" in prompt and "additions" in prompt
    assert "never invent" in prompt.lower()


def test_the_prompt_asks_for_thumbnail_words_a_caption_and_hashtags_with_each_addition():
    prompt = chatgpt_review.build_prompt(SENTENCES, claude_picks(), OPTIONS, title="Friday stream", creator="Kai")

    assert all(f'"{key}"' in prompt for key in ("thumb", "thumb_word", "caption", "hashtags"))
    assert "thumbnail" in prompt and "TikTok caption" in prompt


def test_the_command_is_read_only_ignores_your_settings_and_reads_the_prompt_from_input(tmp_path: Path):
    args = chatgpt_review.command("codex.exe", "gpt-6.2-astra", tmp_path, tmp_path / "answer.txt")

    assert args[:2] == ["codex.exe", "exec"] and args[-1] == "-"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert {"--ephemeral", "--ignore-user-config", "--skip-git-repo-check"} <= set(args)
    assert args[args.index("-m") + 1] == "gpt-6.2-astra"
    assert not any("dangerously" in a for a in args)


def test_the_tool_uses_your_chatgpt_sign_in_never_an_api_key(monkeypatch):  # noqa: ANN001
    for name, value in {"OPENAI_API_KEY": "sk-not-yours", "OPENAI_BASE_URL": "http://127.0.0.1:31415/v1",
                        "ANTHROPIC_BASE_URL": "x", "CLAUDECODE": "1", "CLIPFORGE_DATA_DIR": "D:/x",
                        "PATH": "C:/Windows", "CODEX_HOME": "C:/Users/me/.codex"}.items():
        monkeypatch.setenv(name, value)

    env = chatgpt_review.child_env()

    for gone in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLIPFORGE_DATA_DIR"):
        assert gone not in env, gone
    assert env["PATH"] == "C:/Windows" and env["CODEX_HOME"] == "C:/Users/me/.codex"


def test_the_newest_installed_codex_is_found(monkeypatch, tmp_path: Path):  # noqa: ANN001
    import os
    import time

    monkeypatch.setattr(chatgpt_review.shutil, "which", lambda name: None)
    old, new = tmp_path / "bin" / "aaa" / "codex.exe", tmp_path / "bin" / "bbb" / "codex.exe"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    os.utime(old, (time.time() - 1000, time.time() - 1000))
    monkeypatch.setattr(chatgpt_review, "SEARCH_ROOTS", (tmp_path / "bin",))

    assert chatgpt_review.find_cli("") == str(new)
    monkeypatch.setattr(chatgpt_review, "SEARCH_ROOTS", (tmp_path / "nothing",))
    assert chatgpt_review.find_cli("") is None


# ------------------------------------------------------------------------------------- merging the opinion
def test_agreement_and_disagreement_are_recorded_on_each_clip_and_nothing_is_dropped():
    picks = claude_picks()
    review = chatgpt_review.parse_answer(review_text(
        [{"pick": 1, "verdict": "agree", "reason": "Clear punchline."},
         {"pick": 2, "verdict": "disagree", "reason": "Just settings talk."}]))

    merged, summary = apply_second_opinion(picks, review, WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5),
                                           OPTIONS, duration=END + 5)

    assert [h.title for h in merged] == ["Phone in his hand", "Settings talk"]          # the user decides, not ChatGPT
    first, second = (next(e for e in h.evidence if e["dimension"] == "chatgpt") for h in merged)
    assert first["verdict"] == "agree" and "Clear punchline" in first["text"]
    assert second["verdict"] == "disagree" and "settings talk" in second["text"].lower()
    assert summary == {"agreed": ["Phone in his hand"], "disagreed": [("Settings talk", "Just settings talk.")],
                       "added": []}


def test_a_moment_chatgpt_adds_is_kept_only_if_it_is_new_and_at_most_two_are_added():
    picks = claude_picks()
    review = chatgpt_review.parse_answer(review_text([], [
        {"start": STORY_START + 1, "end": END - 1, "title": "Same story", "score": 90, "reason": "Funny."},
        {"start": 11.0, "end": 18.0, "title": "Loot run", "category": "hype", "score": 70, "reason": "Fast."},
        {"start": 19.0, "end": 28.5, "title": "Map check", "score": 60, "reason": "Calm."},
        {"start": 54.0, "end": 64.0, "title": "Third extra", "score": 50, "reason": "Meh."}]))

    merged, summary = apply_second_opinion(picks, review, WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5),
                                           OPTIONS, duration=END + 5)

    added = [h for h in merged if h.analysis_source == "chatgpt"]
    assert [h.title for h in added] == ["Loot run", "Map check"]
    assert all("ChatGPT" in h.explanation for h in added)
    assert summary["added"] == ["Loot run", "Map check"]
    assert [h.rank for h in merged] == list(range(1, len(merged) + 1))


# ---------------------------------------------------------------------------- fixes from ChatGPT's code review
def test_the_tool_runs_with_its_tools_switched_off_and_the_built_in_openai_provider(tmp_path: Path):
    args = chatgpt_review.command("codex.exe", "gpt-6.2-astra", tmp_path, tmp_path / "answer.txt")

    disabled = {args[i + 1] for i, a in enumerate(args) if a == "--disable"}
    assert {"shell_tool", "unified_exec", "view_image", "browser_use", "computer_use", "apps", "plugins"} <= disabled
    assert 'model_provider="openai"' in args


def test_an_api_key_for_codex_is_never_passed_on(monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("CODEX_API_KEY", "sk-not-yours")
    assert "CODEX_API_KEY" not in chatgpt_review.child_env()


@pytest.mark.parametrize("status", ["Logged in using an API key - sk-proj-***", "Not logged in"])
def test_nothing_is_sent_unless_codex_is_signed_in_with_chatgpt(monkeypatch, status: str):  # noqa: ANN001
    monkeypatch.setattr(chatgpt_review, "find_cli", lambda configured="": "codex.exe")
    monkeypatch.setattr(chatgpt_review, "_login_status", lambda cli, should_cancel=None: status)
    monkeypatch.setattr(chatgpt_review.subprocess, "Popen", lambda *a, **k: pytest.fail("the prompt was sent"))

    with pytest.raises(ChatGPTUnavailable) as refused:
        chatgpt_review.run_codex("a transcript")

    assert ("api key" in refused.value.message.lower()) or ("signed out" in refused.value.message.lower())


def test_a_codex_that_windows_will_not_start_counts_as_unavailable(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(chatgpt_review, "find_cli", lambda configured="": "codex.exe")
    monkeypatch.setattr(chatgpt_review, "_login_status",
                        lambda cli, should_cancel=None: "Logged in using ChatGPT")

    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise PermissionError("This program is blocked by group policy")

    monkeypatch.setattr(chatgpt_review.subprocess, "Popen", blocked)

    with pytest.raises(ChatGPTUnavailable) as failed:
        chatgpt_review.run_codex("a transcript")
    assert "could not be started" in failed.value.message


@pytest.mark.parametrize("answer", ['{"verdicts": 1}', '{"additions": "none"}', '{"verdicts": {"1": "agree"}}'])
def test_answers_of_the_wrong_shape_are_refused_cleanly(answer: str):
    with pytest.raises(ChatGPTUnavailable):
        chatgpt_review.parse_answer(answer)


def test_additions_outside_the_chosen_length_are_left_out():
    picks = claude_picks()
    review = chatgpt_review.parse_answer(review_text([], [
        {"start": 11.0, "end": 18.0, "title": "Too short", "score": 95, "reason": "About 7 seconds."},
        {"start": 11.0, "end": 28.5, "title": "Long enough", "score": 60, "reason": "About 18 seconds."}]))
    fifteen_or_more = HighlightOptions(count=3, min_len=15, max_len=40)

    merged, summary = apply_second_opinion(picks, review, WORDS, SENTENCES, make_audio(END + 5), make_visual(END + 5),
                                           fifteen_or_more, duration=END + 5)

    assert summary["added"] == ["Long enough"]


def test_a_verdict_only_review_does_not_rescore_the_whole_video(monkeypatch):  # noqa: ANN001
    from app.analysis import engine

    picks = claude_picks()
    monkeypatch.setattr(engine, "highlights_from_picks", lambda *a, **k: pytest.fail("rescored for nothing"))
    review = chatgpt_review.parse_answer(review_text([{"pick": 1, "verdict": "agree", "reason": "Good."}]))

    merged, summary = apply_second_opinion(picks, review, WORDS, SENTENCES, None, None, OPTIONS, duration=END + 5)

    assert summary["agreed"] == ["Phone in his hand"] and len(merged) == 2

# ------------------------------------------------------------------------------- second round of the review
def test_only_a_real_program_is_used_never_a_batch_file(monkeypatch, tmp_path: Path):  # noqa: ANN001
    """A codex.cmd from npm would run through the command interpreter, which reads special characters."""
    batch = tmp_path / "codex.cmd"
    batch.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(chatgpt_review.shutil, "which", lambda name: str(batch))
    monkeypatch.setattr(chatgpt_review, "SEARCH_ROOTS", (tmp_path / "nothing",))

    assert chatgpt_review.find_cli("") is None
    assert chatgpt_review.find_cli(str(batch)) is None


@pytest.mark.parametrize("model", ["gpt-6.2-astra", "gpt-6-luna", "o5.1"])
def test_ordinary_model_names_are_accepted(model: str):
    assert chatgpt_review.valid_model(model)


@pytest.mark.parametrize("model", ["gpt & calc", "a|b", "x\"y", "", "-c", "a b"])
def test_model_names_with_odd_characters_are_refused(model: str):
    assert not chatgpt_review.valid_model(model)
    with pytest.raises(ChatGPTUnavailable):
        chatgpt_review.command("codex.exe", model, Path("."), Path("answer.txt"))


def test_web_search_is_switched_off_too(tmp_path: Path):
    args = chatgpt_review.command("codex.exe", "gpt-6.2-astra", tmp_path, tmp_path / "answer.txt")
    assert 'web_search="disabled"' in args


def test_cancelling_during_the_sign_in_check_sends_nothing(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(chatgpt_review, "find_cli", lambda configured="": "codex.exe")
    monkeypatch.setattr(chatgpt_review, "_login_status", lambda cli, should_cancel=None: "Logged in using ChatGPT")
    monkeypatch.setattr(chatgpt_review.subprocess, "Popen", lambda *a, **k: pytest.fail("the prompt was sent"))

    from app.errors import JobCancelled
    with pytest.raises(JobCancelled):
        chatgpt_review.run_codex("a transcript", should_cancel=lambda: True)


def test_an_addition_with_nothing_said_in_it_is_refused():
    """ChatGPT only sees the transcript, so a moment where nobody speaks cannot be one it heard."""
    picks = claude_picks()
    review = chatgpt_review.parse_answer(review_text([], [
        {"start": END + 1, "end": END + 4.5, "title": "Silence", "score": 99, "reason": "Trust me."}]))

    merged, summary = apply_second_opinion(picks, review, WORDS, SENTENCES, make_audio(END + 20), make_visual(END + 20),
                                           HighlightOptions(count=3, min_len=3, max_len=40), duration=END + 20)

    assert summary["added"] == []
