"""The YouTube title and description of a clip, written by Claude when you ask: a title without hashtags, a
description that says what happens and invites comments, the credit on its own line, then #Shorts and the
hashtags. Nothing is saved; you copy it into YouTube. Claude is never really called here: a stand-in answers."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.analysis import claude_review
from app.models import Clip, Video
from tests.test_api import client, clip, upload, uploaded  # noqa: F401  (fixtures shared with the main API tests)


def claude_writes(monkeypatch, title: str, caption: str, hashtags: list[str],  # noqa: ANN001
                  asked: list[str] | None = None) -> None:
    def fake(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        if asked is not None:
            asked.append(prompt)
        return json.dumps({"type": "result", "is_error": False, "result": json.dumps(
            {"title": title, "caption": caption, "hashtags": hashtags})})

    monkeypatch.setattr(claude_review, "run_claude", fake)


@pytest.fixture()
def spoken(client: TestClient, clip: dict) -> dict:  # noqa: F811
    assert client.put("/api/settings", json={"values": {"ai.engine": "claude"}}).status_code == 200
    with db.session_scope() as s:
        row = s.get(Clip, clip["id"])
        row.transcript_text = "Look, sour. Oh my God!"
        row.hashtags = ["#tyler1", "#fyp"]
        s.get(Video, row.video_id).creator_name = "Tyler1"
    return clip


def write(client: TestClient, clip: dict, **options) -> dict:  # noqa: F811
    response = client.post(f"/api/clips/{clip['id']}/youtube-text", json=options)
    assert response.status_code == 200, response.text
    return response.json()


def test_claude_writes_a_title_and_a_description_with_the_credit_shorts_and_hashtags(  # noqa: D103
        client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    asked: list[str] = []
    claude_writes(monkeypatch, "Tyler1 vs German sour candy 😂 #tyler1",
                  "A fan sent Tyler1 sour candy 😂\nIt did not go well.\nWould you try it? 👇",
                  ["#sourcandy", "twitch"], asked)

    text = write(client, spoken)

    assert text["title"] == "Tyler1 vs German sour candy 😂"                      # no hashtags in the title
    assert text["description"] == ("A fan sent Tyler1 sour candy 😂\nIt did not go well.\nWould you try it? 👇\n\n"
                                   "Clip from Tyler1\n\n#Shorts #sourcandy #twitch #tyler1 #fyp")
    assert "Oh my God" in asked[0] and "YouTube" in asked[0] and "comment" in asked[0]


def test_without_emojis_or_hashtags_none_are_left(client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    asked: list[str] = []
    claude_writes(monkeypatch, "Sour candy 😂", "He tried it 😂 #candy\nClip from Tyler1", ["#candy"], asked)

    text = write(client, spoken, emojis=False, hashtags=False)

    assert text == {"title": "Sour candy", "description": "He tried it\n\nClip from Tyler1"}
    assert "No emojis" in asked[0]


def test_a_new_one_is_asked_to_differ_from_the_one_in_the_box(client: TestClient, spoken: dict,  # noqa: F811
                                                              monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_writes(monkeypatch, "New", "New words", [], asked)

    write(client, spoken, avoid="The old description")

    assert "The old description" in asked[0] and "different" in asked[0]


def test_a_long_title_is_cut_to_what_youtube_takes(client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    claude_writes(monkeypatch, "word " * 40, "Words", [])

    assert len(write(client, spoken)["title"]) <= 100


def test_youtube_studio_refuses_angle_brackets_so_none_are_left(client: TestClient, spoken: dict,  # noqa: F811
                                                                 monkeypatch):  # noqa: ANN001
    claude_writes(monkeypatch, "Tyler1 <3 candy", "He loves it <3 >_<", [])

    text = write(client, spoken, hashtags=False)

    assert "<" not in text["title"] + text["description"] and ">" not in text["title"] + text["description"]
    assert text["description"].startswith("He loves it 3 _")


def test_a_title_of_only_hashtags_falls_back_to_the_clips_title(client: TestClient, spoken: dict,  # noqa: F811
                                                                 monkeypatch):  # noqa: ANN001
    claude_writes(monkeypatch, "#tyler1 #candy", "Words", [])

    assert write(client, spoken)["title"] == spoken["title"]


def test_shorts_from_claude_does_not_take_a_hashtag_place_and_the_credit_leaves_the_title(  # noqa: D103
        client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    claude_writes(monkeypatch, "Sour candy – Clip from Tyler1", "Words", ["#shorts", "#a1", "#b2", "#c3", "#d4"])

    text = write(client, spoken)

    assert text["title"] == "Sour candy"
    assert text["description"].endswith("#Shorts #a1 #b2 #c3 #d4 #tyler1 #fyp")


def test_with_local_rules_only_nothing_is_sent_to_claude(client: TestClient, clip: dict, monkeypatch):  # noqa: ANN001, F811
    monkeypatch.setattr(claude_review, "run_claude", lambda *a, **k: pytest.fail("Claude was asked"))
    assert client.put("/api/settings", json={"values": {"ai.engine": "local"}}).status_code == 200

    response = client.post(f"/api/clips/{clip['id']}/youtube-text", json={})

    assert response.status_code == 400 and "switched off" in response.json()["error"]


def test_when_claude_cannot_write_one_it_says_why(client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips.",
                                              hint=claude_review.SIGN_IN_HINT)

    monkeypatch.setattr(claude_review, "run_claude", signed_out)

    response = client.post(f"/api/clips/{spoken['id']}/youtube-text", json={})

    assert response.status_code == 503 and "signed out" in response.json()["error"]
