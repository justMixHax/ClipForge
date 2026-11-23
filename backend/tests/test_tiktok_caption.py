"""The TikTok caption of a clip: Claude rewrites it when you ask (with or without emojis, with or without hashtags),
and you can write or change it yourself. Claude is never really called here: a stand-in answers instead."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.analysis import claude_review
from app.models import Clip, Video
from tests.test_api import client, clip, upload, uploaded  # noqa: F401  (fixtures shared with the main API tests)


def claude_writes(monkeypatch, caption: str, hashtags: list[str], asked: list[str] | None = None) -> None:  # noqa: ANN001
    def fake(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        if asked is not None:
            asked.append(prompt)
        return json.dumps({"type": "result", "is_error": False, "result": json.dumps(
            {"title": "Robbing a store", "caption": caption, "hashtags": hashtags})})

    monkeypatch.setattr(claude_review, "run_claude", fake)


@pytest.fixture()
def spoken(client: TestClient, clip: dict) -> dict:  # noqa: F811
    """A clip with something said in it, from a named creator, with Claude as the engine."""
    assert client.put("/api/settings", json={"values": {"ai.engine": "claude"}}).status_code == 200
    with db.session_scope() as s:
        row = s.get(Clip, clip["id"])
        row.transcript_text = "Step one, walk in. Step two, the civilian calls the cops."
        row.hashtags = ["#robrun", "#chewy", "#fyp"]
        s.get(Video, row.video_id).creator_name = "Chewy"
    return clip


def rewrite(client: TestClient, clip: dict, **options) -> str:  # noqa: F811
    response = client.post(f"/api/clips/{clip['id']}/tiktok-caption", json=options)
    assert response.status_code == 200, response.text
    return response.json()["caption"]


# ---------------------------------------------------------------------------------------- Claude rewrites it
def test_claude_rewrites_the_caption_with_emojis_the_credit_and_hashtags(client: TestClient, spoken: dict,  # noqa: F811
                                                                        monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_writes(monkeypatch, "Robbing a store step by step 😂 watch the civilian 👀", ["#gta", "heist"], asked)

    text = rewrite(client, spoken, emojis=True, hashtags=True)

    assert text == ("Robbing a store step by step 😂 watch the civilian 👀\nClip from Chewy\n\n"
                    "#gta #heist #chewy #robrun #fyp")
    assert "civilian calls the cops" in asked[0] and "one to three emojis" in asked[0]
    assert "different from this one" in asked[0] and "My pick" in asked[0]          # a new one each time


def test_without_emojis_or_hashtags_none_are_left_even_if_claude_adds_some(client: TestClient, spoken: dict,  # noqa: F811
                                                                         monkeypatch):  # noqa: ANN001
    asked: list[str] = []
    claude_writes(monkeypatch, "Robbing a store step by step 😂🔥 #gta watch the civilian", ["#gta"], asked)

    text = rewrite(client, spoken, emojis=False, hashtags=False)

    assert text == "Robbing a store step by step watch the civilian\nClip from Chewy"
    assert "No emojis" in asked[0]


def test_the_credit_gets_its_own_line_and_the_creators_tag_stays_among_many(client: TestClient,  # noqa: F811
                                                                          spoken: dict, monkeypatch):  # noqa: ANN001
    claude_writes(monkeypatch, "Watch this heist 🔥 — Clip from Chewy.", ["#a1", "#b2", "#c3", "#d4", "#e5"])

    text = rewrite(client, spoken)

    assert text == "Watch this heist 🔥\nClip from Chewy\n\n#a1 #b2 #c3 #d4 #chewy #robrun"


def test_nothing_is_saved_until_you_save_it(client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    claude_writes(monkeypatch, "A brand new caption", [])

    rewrite(client, spoken)

    assert client.get(f"/api/clips/{spoken['id']}").json()["tiktok_caption"] == ""


def test_when_claude_cannot_write_one_it_says_why(client: TestClient, spoken: dict, monkeypatch):  # noqa: ANN001, F811
    def signed_out(prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        raise claude_review.ClaudeUnavailable("Claude is signed out on this PC, so the local rules picked the clips.",
                                              hint=claude_review.SIGN_IN_HINT)

    monkeypatch.setattr(claude_review, "run_claude", signed_out)

    response = client.post(f"/api/clips/{spoken['id']}/tiktok-caption", json={})

    assert response.status_code == 503 and "signed out" in response.json()["error"]


def test_with_local_rules_only_nothing_is_sent_to_claude(client: TestClient, clip: dict, monkeypatch):  # noqa: ANN001, F811
    monkeypatch.setattr(claude_review, "run_claude", lambda *a, **k: pytest.fail("Claude was asked"))
    assert client.put("/api/settings", json={"values": {"ai.engine": "local"}}).status_code == 200

    response = client.post(f"/api/clips/{clip['id']}/tiktok-caption", json={})

    assert response.status_code == 400 and "switched off" in response.json()["error"]


# -------------------------------------------------------------------------------------- you write it yourself
def test_your_own_caption_is_kept_exactly_as_you_wrote_it(client: TestClient, spoken: dict):  # noqa: F811
    mine = "My own words 🔥\n\nwith #tags in the middle\nClip from Chewy"

    saved = client.patch(f"/api/clips/{spoken['id']}", json={"tiktok_caption": mine})

    assert saved.status_code == 200, saved.text
    assert saved.json()["tiktok_caption"] == mine == saved.json()["post_caption"]
    assert mine in client.get(f"/api/clips/{spoken['id']}/post-kit").text


def test_an_empty_caption_goes_back_to_the_automatic_one(client: TestClient, spoken: dict):  # noqa: F811
    client.patch(f"/api/clips/{spoken['id']}", json={"tiktok_caption": "Mine"})

    back = client.patch(f"/api/clips/{spoken['id']}", json={"tiktok_caption": "  "}).json()

    assert back["tiktok_caption"] == "" and back["post_caption"].startswith("My pick")
    assert "Clip from Chewy" in back["post_caption"] and "#robrun" in back["post_caption"]


def test_a_caption_longer_than_tiktok_allows_is_refused(client: TestClient, clip: dict):  # noqa: F811
    assert client.patch(f"/api/clips/{clip['id']}", json={"tiktok_caption": "x" * 4001}).status_code == 422
