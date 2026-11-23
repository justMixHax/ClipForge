"""Reading the JSON an AI writes into its answer. Seen for real: Claude wrote a placeholder, then "Correction, here
is the real answer:" with its real picks; and Claude wrote times as a clock (28:35) instead of seconds. Both times
its good picks were thrown away and the local rules picked instead. The same reading serves Claude's picks,
ChatGPT's second opinion, the post text and the prompt edit."""
from __future__ import annotations

import json

import pytest

from app.analysis import chatgpt_review, claude_review
from app.analysis.answers import json_answers, seconds
from app.editing import prompt_edit
from app.publishing import assistant

# Claude's answer for "I Hired A R6 Pro to SECRECTLY Troll Streamers" (22 November 2026), shortened
DRAFT_THEN_CORRECTION = """```json
{"picks": [
{"start": 45.0, "end": 45.0, "title": "placeholder", "hook": "placeholder", "category": "funny", "score": 0, "reason": "placeholder"}
]}
```

Correction, here is the real answer:

```json
{"picks": [
{"start": 2710.0, "end": 2732.0, "title": "Pause the timer, I need to poop", "hook": "Mid-match bathroom break", "category": "funny", "score": 86, "reason": "A troll that lands."},
{"start": 1204.5, "end": 1239.0, "title": "Squirtle finds out who he beat", "hook": "He had no idea", "category": "reaction", "score": 80, "reason": "A reveal."}
]}
```"""

# Claude's answer for "Fan Mail is Over..." (23 November 2026): times as a clock, not quoted
CLOCK_TIMES = """{"picks": [{"start": 28:35, "end": 29:28, "title": "Why Is The Sock Hard?", "hook": "Fan mail goes very wrong", "category": "reaction", "score": 88, "reason": "A package opens with an emote joke, then the sock turns out to be hard."}]}

Correction: the times above are minutes and seconds."""


def printed(text: str) -> str:
    """What the Claude Code tool prints with --output-format json."""
    return json.dumps({"type": "result", "is_error": False, "result": text})


# ------------------------------------------------------------------------------------------- the reader
def test_every_json_block_is_found_the_last_one_first():
    found = list(json_answers('a {draft} then {"n": 1} and finally {"n": 2}'))

    assert found[:2] == [{"n": 2}, {"n": 1}]


def test_clock_times_left_unquoted_are_read_once_quoted():
    found = list(json_answers('{"start": 28:35, "end": 1:02:15.5, "leave_out": [[1:20, 1:35]]}'))

    assert found[0] == {"start": "28:35", "end": "1:02:15.5", "leave_out": [["1:20", "1:35"]]}


def test_a_clock_inside_a_sentence_is_never_touched():
    found = list(json_answers('{"reason": "he says: 12:30, then leaves", "start": 5}'))

    assert found[0] == {"reason": "he says: 12:30, then leaves", "start": 5}


@pytest.mark.parametrize("value, expected", [
    (12.5, 12.5), ("12.5", 12.5), ("28:35", 1715.0), ("1:02:15.5", 3735.5), ("0:07", 7.0),
    (True, None), ("soon", None), (None, None), (float("nan"), None), ("", None),
])
def test_times_are_read_as_seconds_or_as_a_clock(value: object, expected: float | None):
    assert seconds(value) == expected


# ------------------------------------------------------------------------------------- Claude's picks
def test_claudes_correction_after_a_draft_is_used_not_thrown_away():
    picks = claude_review.parse_answer(printed(DRAFT_THEN_CORRECTION))

    assert [(p.start, p.title) for p in picks] == [(2710.0, "Pause the timer, I need to poop"),
                                                   (1204.5, "Squirtle finds out who he beat")]


def test_claudes_clock_times_are_read_as_seconds():
    picks = claude_review.parse_answer(printed(CLOCK_TIMES))

    assert [(p.start, p.end, p.title) for p in picks] == [(1715.0, 1768.0, "Why Is The Sock Hard?")]


def test_braces_in_the_text_around_claudes_answer_do_not_hide_it():
    text = 'I looked for {setup, payoff} pairs.\n{"picks": [{"start": 10, "end": 30, "title": "A"}]}\nDone {ok}.'

    assert [p.title for p in claude_review.parse_answer(printed(text))] == ["A"]


def test_an_answer_with_no_usable_pick_is_still_reported():
    with pytest.raises(claude_review.ClaudeUnavailable, match="no picks"):
        claude_review.parse_answer(printed('{"picks": [{"start": 45.0, "end": 45.0, "title": "placeholder"}]}'))


# ------------------------------------------------------------------------------------ ChatGPT's opinion
def test_chatgpts_correction_after_a_draft_is_used_and_clock_times_are_read():
    text = ('{"verdicts": [{"pick": 1, "verdict": "agree"}], "additions": []}\n'
            'Correction:\n{"verdicts": [{"pick": 1, "verdict": "disagree", "reason": "No payoff."}], '
            '"additions": [{"start": 12:00, "end": 12:40, "title": "Loot run"}]}')

    review = chatgpt_review.parse_answer(text)

    assert review.verdicts == {1: ("disagree", "No payoff.")}
    assert [(a.start, a.end) for a in review.additions] == [(720.0, 760.0)]


# --------------------------------------------------------------------- the post text and the prompt edit
def test_the_post_text_is_read_past_braces_and_a_draft():
    text = 'Draft: {"title": "x"} {oops}\nFinal: {"title": "Robbing a store", "caption": "Step by step", "hashtags": ["#gta"]}'

    assert assistant.parse(printed(text))["title"] == "Robbing a store"


def test_the_prompt_edit_answer_is_read_past_braces_and_a_draft():
    text = ('Plan {step one}: {"changes": {"title": "draft"}}\n'
            'Corrected: {"changes": {"start": 1:20, "subtitles": {"size": 120}}, "not_possible": [], "summary": "ok"}')

    answer = prompt_edit.parse_answer(printed(text))

    assert answer.changes == {"start": "1:20", "subtitles": {"size": 120}} and answer.summary == "ok"
