"""Your own presets: the clipping options on the AI Clipper page saved under a name, to start from next time. They
are checked by the same rules as an analysis, so a saved preset always works when it is used."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_api import client  # noqa: F401  (the shared app fixture)

MINE = {"content_preset": "gaming", "game_style": "gta", "count": 4, "length": "15-60", "min_score": 60,
        "aspect": "9:16", "layout": "facecam_top", "captions": "gaming", "quality": "speed", "auto_render": True,
        "remove_silence": True, "show_hook": False}


def save(client: TestClient, name: str, config: dict | None = None):  # noqa: F811, ANN201
    return client.post("/api/presets", json={"name": name, "config": MINE if config is None else config})


def test_a_preset_is_saved_listed_and_removed(client: TestClient):  # noqa: F811
    saved = save(client, "  My GTA streams  ")

    assert saved.status_code == 200, saved.text
    preset = saved.json()
    assert preset["name"] == "My GTA streams" and preset["config"] == MINE
    assert [p["name"] for p in client.get("/api/presets").json()] == ["My GTA streams"]

    assert client.delete(f"/api/presets/{preset['id']}").status_code == 200
    assert client.get("/api/presets").json() == []
    assert client.delete(f"/api/presets/{preset['id']}").status_code == 404


def test_saving_under_the_same_name_updates_it(client: TestClient):  # noqa: F811
    first = save(client, "Podcast").json()

    again = save(client, "podcast", {**MINE, "count": 8}).json()           # whatever its capitals

    assert again["id"] == first["id"] and again["config"]["count"] == 8
    assert len(client.get("/api/presets").json()) == 1


def test_presets_are_listed_by_name(client: TestClient):  # noqa: F811
    for name in ("Zebra", "alpha", "Middle"):
        assert save(client, name).status_code == 200

    assert [p["name"] for p in client.get("/api/presets").json()] == ["alpha", "Middle", "Zebra"]


@pytest.mark.parametrize("change", [
    {"count": 0}, {"count": 31}, {"length": "1-2 hours"}, {"captions": "comic-sans"}, {"layout": "sideways"},
    {"game_style": "tetris"}, {"aspect": "2:1"}, {"surprise": True}, {"min_score": 101},
])
def test_a_preset_that_would_not_work_is_refused(client: TestClient, change: dict):  # noqa: F811
    assert save(client, "Broken", {**MINE, **change}).status_code in (400, 422)
    assert client.get("/api/presets").json() == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_a_preset_needs_a_short_name(client: TestClient, name: str):  # noqa: F811
    assert save(client, name).status_code in (400, 422)


def test_a_preset_may_leave_options_out_and_gets_the_usual_ones(client: TestClient):  # noqa: F811
    preset = save(client, "Few options", {"count": 3, "captions": "minimal"}).json()

    assert preset["config"]["count"] == 3 and preset["config"]["captions"] == "minimal"
    assert preset["config"]["length"] == "15-60" and preset["config"]["auto_render"] is True


def test_there_is_a_limit_of_fifty(client: TestClient):  # noqa: F811
    for number in range(50):
        assert save(client, f"Preset {number}").status_code == 200

    refused = save(client, "One too many")

    assert refused.status_code == 400 and "50" in refused.json()["error"]
    assert save(client, "Preset 7", {**MINE, "count": 2}).status_code == 200   # updating one is still fine
