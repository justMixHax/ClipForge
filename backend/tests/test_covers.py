"""Thumbnails through the HTTP API: seeing and downloading a clip's thumbnail, changing its words, its coloured
word and its moment, and the TikTok caption that goes with it. Renders are real (tiny) FFmpeg renders."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.models import Clip
from app.services import storage
from tests.test_api import client, clip, upload, uploaded  # noqa: F401  (fixtures shared with the main API tests)
from tests.test_api_library import render_now

pytestmark = pytest.mark.ffmpeg


def stored(clip_id: int) -> Clip:
    with db.session_scope() as s:
        row = s.get(Clip, clip_id)
        s.expunge(row)
        return row


@pytest.fixture()
def rendered(client: TestClient, clip: dict) -> dict:  # noqa: F811
    return render_now(client, clip["id"])


def test_a_rendered_clip_comes_with_its_thumbnail_words_and_tiktok_caption(rendered: dict):
    assert rendered["cover_url"].startswith(f"/api/clips/{rendered['id']}/cover?v=")
    assert (rendered["cover_text"], rendered["cover_word"]) == ("MY PICK", "PICK")      # from the title
    assert rendered["start"] <= rendered["cover_time"] <= rendered["end"]
    assert rendered["post_caption"].startswith("My pick")


def test_the_thumbnail_can_be_seen_and_downloaded(client: TestClient, rendered: dict):  # noqa: F811
    shown = client.get(rendered["cover_url"])
    saved = client.get(f"/api/clips/{rendered['id']}/cover", params={"download": 1})

    assert shown.status_code == 200 and shown.headers["content-type"] == "image/jpeg"
    assert Image.open(storage.data_path(stored(rendered["id"]).cover_path)).size == (1080, 1920)
    assert saved.headers["content-disposition"].startswith("attachment") and "thumbnail.jpg" in saved.headers[
        "content-disposition"]


def test_you_can_change_the_words_the_coloured_word_and_the_moment(client: TestClient, rendered: dict):  # noqa: F811
    picture = storage.data_path(stored(rendered["id"]).cover_path)
    before = picture.read_bytes()

    response = client.post(f"/api/clips/{rendered['id']}/cover", json={"text": "he found it", "word": "found",
                                                                         "time": 1.5})

    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["cover_text"], out["cover_word"], out["cover_time"]) == ("HE FOUND IT", "FOUND", 1.5)
    assert picture.read_bytes() != before and out["cover_url"] != rendered["cover_url"]


def test_forge_can_pick_the_moment_again_and_empty_words_go_back_to_the_title(client: TestClient,  # noqa: F811
                                                                               rendered: dict):
    client.post(f"/api/clips/{rendered['id']}/cover", json={"text": "he found it", "time": 2.4})

    out = client.post(f"/api/clips/{rendered['id']}/cover", json={"text": "", "auto": True}).json()

    assert (out["cover_text"], out["cover_word"]) == ("MY PICK", "PICK")
    assert rendered["start"] <= out["cover_time"] <= rendered["end"]


@pytest.mark.parametrize(("body", "says"), [
    ({"time": 9.0}, "inside the clip"),
    ({"time": -1.0}, "inside the clip"),
    ({"text": "x" * 300}, "Invalid request"),
])
def test_a_moment_outside_the_clip_or_too_many_words_is_refused(client: TestClient, rendered: dict,  # noqa: F811
                                                                body: dict, says: str):
    response = client.post(f"/api/clips/{rendered['id']}/cover", json=body)

    assert response.status_code in (400, 422) and says in response.json()["error"]


def test_words_the_fonts_here_cannot_draw_are_refused_with_a_reason(client: TestClient, rendered: dict):  # noqa: F811
    response = client.post(f"/api/clips/{rendered['id']}/cover", json={"text": "مرحبا بالعالم"})

    assert response.status_code == 400 and "cannot draw" in response.json()["error"]


def test_before_a_render_the_words_are_kept_for_it_and_there_is_no_picture_yet(client: TestClient,  # noqa: F811
                                                                                clip: dict):  # noqa: F811
    response = client.post(f"/api/clips/{clip['id']}/cover", json={"text": "watch him", "word": "him"})

    assert response.status_code == 200, response.text
    assert (response.json()["cover_text"], response.json()["cover_url"]) == ("WATCH HIM", "")
    assert client.get(f"/api/clips/{clip['id']}/cover").status_code == 404
    after = render_now(client, clip["id"])
    assert after["cover_text"] == "WATCH HIM" and after["cover_url"]


def test_export_copies_the_thumbnail_next_to_the_clip(client: TestClient, rendered: dict):  # noqa: F811
    result = client.post("/api/clips/export", json={"ids": [rendered["id"]]}).json()

    assert Image.open(Path(result["folder"]) / "01 - My pick.jpg").size == (1080, 1920)
