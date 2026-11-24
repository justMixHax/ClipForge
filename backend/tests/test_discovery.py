"""Creator Discovery through the real application: finding and following channels, looking at their uploads,
what the public data says about them, and importing what you may clip. YouTube is a local stand-in here
(tests/fake_youtube_data.py); the real API is never called."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from app.discovery import service
from app.jobs import pipeline, queue
from app.models import Clip, Creator, CreatorVideo, Job, Notification, Secret, Setting, Video
from app.services import storage
from tests.fake_youtube_data import KEY, FakeYouTubeData, youtube  # noqa: F401  (fixture)

BASE = "http://127.0.0.1:3000"
JYNXZI = "UCjiXtODGCCulmhwypZAWSag"
TYLER = "UCwV_0HmQkRrTcrReaMxPeDw"


def ago(days: float = 0, hours: float = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def client(data_dir, youtube: FakeYouTubeData):  # noqa: ANN001, ANN201, F811
    from app.main import create_app

    with TestClient(create_app(), base_url=BASE) as c:
        yield c


@pytest.fixture()
def keyed(client: TestClient) -> TestClient:
    assert client.put("/api/settings/secret", json={"name": "youtube.api_key", "value": KEY}).status_code == 200
    return client


@pytest.fixture()
def jynxzi(youtube: FakeYouTubeData) -> FakeYouTubeData:  # noqa: F811
    """A channel with five long videos, a Short, a stream that is live and one that has not started."""
    youtube.add_channel(JYNXZI, "Jynxzi", "@Jynxzi", subscribers=6_500_000)
    youtube.add_video("stream00005", JYNXZI, "Ranked grind", minutes=110, views=950_000, published=ago(12))
    youtube.add_video("stream00004", JYNXZI, "Late night siege", minutes=100, views=1_100_000, published=ago(10))
    youtube.add_video("stream00003", JYNXZI, "Comments are off here", minutes=95, views=1_000_000, published=ago(8))
    youtube.add_video("stream00002", JYNXZI, "Ranked with chat", minutes=120, views=900_000, published=ago(5))
    youtube.add_video("stream00001", JYNXZI, "20 WOMEN VS 1 STREAMER", minutes=151, views=3_100_000, published=ago(3),
                      was_live=True)
    youtube.add_video("short000001", JYNXZI, "He said WHAT", minutes=0.5, views=5_000_000, published=ago(1))
    youtube.add_video("live0000001", JYNXZI, "LIVE: ranked", live="live", published=ago(hours=1))
    youtube.add_video("soon0000001", JYNXZI, "Tomorrow's stream", live="upcoming", published=ago(hours=2))
    youtube.comments["stream00001"] = [("12:34 he fell off the chair 😂", 1200), ("LMAO 12:40", 30),
                                       ("the scam call at 41:05 is gold", 250), ("W stream", 12), ("lol", 3)]
    youtube.comments["stream00002"] = [("good stream", 5), ("nice", 1)]
    youtube.comments_off.add("stream00003")
    return youtube


def follow(client: TestClient, channel_id: str = JYNXZI, **body) -> dict:
    response = client.post("/api/discovery/creators", json={"channel_id": channel_id, **body})
    assert response.status_code == 200, response.text
    return response.json()


def videos(client: TestClient, **params) -> list[dict]:
    response = client.get("/api/discovery/videos", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def units_used(client: TestClient) -> int:
    return client.get("/api/discovery").json()["usage"]["units"]


# ------------------------------------------------------------------------------------------------- the key
def test_without_a_key_it_says_what_it_needs_and_asks_youtube_nothing(client: TestClient, jynxzi: FakeYouTubeData):
    overview = client.get("/api/discovery").json()
    refused = client.post("/api/discovery/find", json={"text": "@Jynxzi"})

    assert overview["key_set"] is False and overview["creators"] == [] and overview["suggestions"] == []
    assert refused.status_code == 400 and "Add your YouTube Data API key first" in refused.json()["error"]
    assert client.post("/api/discovery/check").status_code == 400
    assert jynxzi.asked == []


def test_the_key_is_kept_encrypted_and_never_comes_back(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)

    pages = [keyed.get(path).text for path in ("/api/discovery", "/api/discovery/videos", "/api/settings",
                                               "/api/system/features")]
    assert all(KEY not in page for page in pages)
    with db.session_scope() as s:
        assert KEY.encode() not in s.get(Secret, "youtube.api_key").ciphertext
    assert keyed.get("/api/discovery").json()["key_set"] is True


def test_a_key_can_be_tried_out_before_following_anything(keyed: TestClient, youtube: FakeYouTubeData):  # noqa: F811
    youtube.add_channel(service.KEY_CHECK_CHANNEL, "Google for Developers", "@GoogleDevelopers")

    worked = keyed.post("/api/discovery/try-key")

    assert worked.status_code == 200 and worked.json() == {"ok": True, "saw": "Google for Developers"}
    assert units_used(keyed) == 1
    keyed.put("/api/settings/secret", json={"name": "youtube.api_key", "value": "AIza-wrong"})
    refused = keyed.post("/api/discovery/try-key")
    assert refused.status_code == 400 and "did not accept your API key" in refused.json()["error"]
    assert "Credentials" in refused.json()["hint"]


# ------------------------------------------------------------------------------------------------- finding
def test_a_channel_is_found_from_its_link_for_one_unit(keyed: TestClient, jynxzi: FakeYouTubeData):
    found = keyed.post("/api/discovery/find", json={"text": "https://www.youtube.com/@Jynxzi/streams"}).json()

    (channel,) = found["channels"]
    assert (channel["id"], channel["title"], channel["handle"], channel["subscribers"]) == (
        JYNXZI, "Jynxzi", "@jynxzi", 6_500_000)
    assert channel["creator_id"] is None and found["units"] == 1 and units_used(keyed) == 1


def test_a_channel_is_found_from_a_link_to_one_of_its_videos(keyed: TestClient, jynxzi: FakeYouTubeData):
    found = keyed.post("/api/discovery/find", json={"text": "https://youtu.be/stream00001?t=754"}).json()

    assert [c["id"] for c in found["channels"]] == [JYNXZI] and found["units"] == 2


def test_a_name_is_only_searched_for_when_you_ask_because_it_costs_a_hundred_units(keyed: TestClient,
                                                                                    jynxzi: FakeYouTubeData):
    refused = keyed.post("/api/discovery/find", json={"text": "jynxzi"})

    assert refused.status_code == 400 and "100 of your 10,000" in refused.json()["hint"]
    assert jynxzi.asked == [] and units_used(keyed) == 0
    found = keyed.post("/api/discovery/find", json={"text": "jynx", "search": True}).json()
    assert [c["title"] for c in found["channels"]] == ["Jynxzi"] and found["units"] == 101 == units_used(keyed)


@pytest.mark.parametrize("text, said", [("https://www.twitch.tv/jynxzi", "not a YouTube channel or video"),
                                        ("   ", "Paste a channel")])
def test_what_is_not_a_youtube_channel_is_refused_without_asking_youtube(keyed: TestClient, jynxzi: FakeYouTubeData,
                                                                         text: str, said: str):
    refused = keyed.post("/api/discovery/find", json={"text": text})

    assert refused.status_code == 400 and said in refused.json()["error"] and jynxzi.asked == []


def test_a_channel_youtube_does_not_know_is_said_plainly(keyed: TestClient, jynxzi: FakeYouTubeData):
    missing = keyed.post("/api/discovery/find", json={"text": "@nobody-by-this-name"})
    nothing = keyed.post("/api/discovery/find", json={"text": "zzzz", "search": True})

    assert missing.status_code == 404 and "no channel with that name" in missing.json()["error"]
    assert nothing.status_code == 404 and "No channel was found for “zzzz”" in nothing.json()["error"]


# ----------------------------------------------------------------------------------------------- following
def test_following_a_channel_takes_a_first_look_at_its_uploads(keyed: TestClient, jynxzi: FakeYouTubeData):
    result = follow(keyed, permission_status="licensed", permission_note="Clipping campaign on his Discord")

    creator = result["creator"]
    assert (creator["title"], creator["handle"], creator["subscribers"]) == ("Jynxzi", "@jynxzi", 6_500_000)
    assert (creator["permission_status"], creator["permission_note"]) == ("licensed", "Clipping campaign on his Discord")
    assert creator["checked_at"] and creator["check_error"] == "" and creator["videos_listed"] == 5
    assert result["first_look"] == {"creator_id": creator["id"], "found": 8, "new": 5, "first": True,
                                    "comments_read": 5}
    # the channel, its uploads, their numbers, and the top comments of the five long videos
    assert jynxzi.units() == 8 == units_used(keyed)
    assert [a.resource for a in jynxzi.asked] == ["channels", "playlistItems", "videos"] + ["commentThreads"] * 5


def test_the_list_shows_long_videos_newest_first_and_leaves_out_shorts(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)

    listed = videos(keyed)

    assert [v["external_id"] for v in listed] == ["live0000001", "stream00001", "stream00002", "stream00003",
                                                  "stream00004", "stream00005"]
    stream = listed[1]
    assert (stream["title"], stream["duration"], stream["views"], stream["was_live"]) == (
        "20 WOMEN VS 1 STREAMER", 9060.0, 3_100_000, True)
    assert stream["url"] == "https://www.youtube.com/watch?v=stream00001" and stream["creator_title"] == "Jynxzi"
    assert stream["library"] is None and stream["importing"] is None and stream["status"] == "new"
    assert "short000001" in [v["external_id"] for v in videos(keyed, min_minutes=0)]
    assert "soon0000001" in [v["external_id"] for v in videos(keyed, show="all")]


def test_what_the_top_comments_say_is_counted_not_guessed(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)

    by_id = {v["external_id"]: v for v in videos(keyed)}

    said = by_id["stream00001"]["signals"]
    assert (said["checked"], said["laughing"], said["off"]) == (5, 3, False)
    assert [(m["t"], m["mentions"], m["likes"], m["laughing"]) for m in said["moments"]] == [
        (754.0, 2, 1230, True), (2465.0, 1, 250, False)]
    assert said["moments"][0]["text"] == "12:34 he fell off the chair 😂"
    assert said["moments"][0]["url"] == "https://www.youtube.com/watch?v=stream00001&t=754s"
    assert (by_id["stream00002"]["signals"]["checked"], by_id["stream00002"]["signals"]["laughing"]) == (2, 0)
    assert by_id["stream00003"]["signals"]["off"] is True              # comments are switched off there
    assert by_id["stream00004"]["signals"]["checked"] == 0             # none written yet
    assert by_id["live0000001"]["signals"] is None                     # a stream still running is not read


def test_a_video_stands_out_against_the_channels_other_long_videos(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)

    by_id = {v["external_id"]: v for v in videos(keyed, min_minutes=0)}

    assert by_id["stream00001"]["standout"] == 3.18                    # 3.1 M against a usual 975,000
    assert by_id["stream00002"]["standout"] == 0.86
    assert by_id["short000001"]["standout"] is None                    # a Short is not compared with streams
    assert by_id["live0000001"]["standout"] is None
    assert by_id["live0000001"]["fresh"] is True and by_id["stream00001"]["fresh"] is False


def test_the_list_can_be_ordered_by_what_the_public_data_says(keyed: TestClient, jynxzi: FakeYouTubeData):
    jynxzi.comments["stream00004"] = [("hahaha", 1)] * 18 + [("ok", 0)] * 2 + [("1:00:00 clip that", 9000)]
    follow(keyed)

    assert videos(keyed, sort="standout")[0]["external_id"] == "stream00001"
    assert [v["external_id"] for v in videos(keyed, sort="moments")][:2] == ["stream00004", "stream00001"]
    # laughing comments only count from twenty comments read: three of five says too little
    assert videos(keyed, sort="laughs")[0]["external_id"] == "stream00004"
    assert keyed.get("/api/discovery/videos", params={"sort": "loudest"}).status_code == 400


def test_following_the_same_channel_twice_is_refused(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)

    again = keyed.post("/api/discovery/creators", json={"channel_id": JYNXZI})

    assert again.status_code == 409 and "already follow Jynxzi" in again.json()["error"]


def test_what_is_not_a_channel_id_cannot_be_followed(keyed: TestClient, jynxzi: FakeYouTubeData):
    assert keyed.post("/api/discovery/creators", json={"channel_id": "x" * 24}).status_code == 400
    assert keyed.post("/api/discovery/creators", json={"channel_id": "UCshort"}).status_code == 422
    unknown = keyed.post("/api/discovery/creators", json={"channel_id": "UC" + "y" * 22})
    assert unknown.status_code == 404 and keyed.get("/api/discovery").json()["creators"] == []


def test_your_right_to_clip_a_creator_is_a_record_you_keep(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    assert creator["permission_status"] == "unknown"

    changed = keyed.patch(f"/api/discovery/creators/{creator['id']}",
                          json={"permission_status": "permission", "permission_note": "He said so on stream"})

    assert changed.status_code == 200
    assert (changed.json()["permission_status"], changed.json()["permission_note"]) == ("permission", "He said so on stream")
    assert keyed.patch(f"/api/discovery/creators/{creator['id']}", json={"permission_status": "maybe"}).status_code == 422
    assert keyed.patch("/api/discovery/creators/999", json={"permission_note": "x"}).status_code == 404


def test_unfollowing_forgets_what_was_found_but_keeps_your_library(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    with db.session_scope() as s:
        s.add(Video(title="Imported earlier", original_filename="a.mp4", storage_key=storage.new_key(),
                    storage_mode="linked", file_path="C:/nowhere/a.mp4", duration=9060, width=1920, height=1080,
                    source_type="url", source_url="https://www.youtube.com/watch?v=stream00001",
                    creator_name="Jynxzi", permission_status="licensed"))

    assert keyed.delete(f"/api/discovery/creators/{creator['id']}").json() == {"unfollowed": True}

    with db.session_scope() as s:
        assert s.query(Creator).count() == 0 and s.query(CreatorVideo).count() == 0
        assert s.query(Video).count() == 1
    assert videos(keyed, show="all") == []


# ------------------------------------------------------------------------------------------------ checking
def test_checking_again_finds_new_uploads_and_brings_the_numbers_up_to_date(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    jynxzi.add_video("stream00006", JYNXZI, "Brand new stream", minutes=140, views=40_000, published=ago(hours=3))
    jynxzi.videos["stream00001"]["views"] = 3_400_000
    before = len(jynxzi.asked)

    result = keyed.post(f"/api/discovery/creators/{creator['id']}/check").json()

    assert (result["summary"]["new"], result["summary"]["first"]) == (1, False)
    by_id = {v["external_id"]: v for v in videos(keyed)}
    assert by_id["stream00006"]["fresh"] is True and by_id["stream00001"]["views"] == 3_400_000
    # comments read less than a day ago are not read again: only the new video's are
    assert [a.resource for a in jynxzi.asked[before:]] == ["channels", "playlistItems", "videos", "commentThreads"]
    assert jynxzi.asked[-1].query["videoId"] == "stream00006"


def test_comments_of_a_young_video_are_read_again_after_most_of_a_day(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    old = (datetime.now(timezone.utc) - timedelta(hours=21)).isoformat()
    with db.session_scope() as s:
        for row in s.query(CreatorVideo):
            if row.signals:
                row.signals = {**row.signals, "fetched_at": old}
    jynxzi.comments["stream00002"].append(("59:10 LMAO", 80))
    before = len(jynxzi.asked)

    keyed.post(f"/api/discovery/creators/{creator['id']}/check")

    assert [a.resource for a in jynxzi.asked[before:]].count("commentThreads") == 5
    said = {v["external_id"]: v for v in videos(keyed)}["stream00002"]["signals"]
    assert said["laughing"] == 1 and said["moments"][0]["t"] == 3550.0


def test_what_youtube_has_not_confirmed_for_thirty_days_is_not_kept(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    with db.session_scope() as s:
        s.add(CreatorVideo(creator_id=creator["id"], external_id="ancient0001", title="An old one", duration=3600,
                           stats_at=datetime.now(timezone.utc) - timedelta(days=31)))
        s.add(CreatorVideo(creator_id=creator["id"], external_id="recent00001", title="Still fine", duration=3600,
                           stats_at=datetime.now(timezone.utc) - timedelta(days=29)))

    keyed.post(f"/api/discovery/creators/{creator['id']}/check")

    kept = {v["external_id"] for v in videos(keyed, show="all", min_minutes=0)}
    assert "ancient0001" not in kept and "recent00001" in kept


def test_a_channel_that_is_gone_says_so_on_its_card(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    del jynxzi.channels[JYNXZI]

    refused = keyed.post(f"/api/discovery/creators/{creator['id']}/check")

    assert refused.status_code == 400 and "no longer lists this channel" in refused.json()["error"]
    card = keyed.get("/api/discovery").json()["creators"][0]
    assert "no longer lists this channel" in card["check_error"] and len(videos(keyed)) == 6   # what was found stays


def test_when_the_quota_runs_out_half_way_what_was_read_is_kept(keyed: TestClient, jynxzi: FakeYouTubeData):
    jynxzi.units_left = 5       # the channel, its uploads, their numbers and two videos' comments

    result = follow(keyed)

    assert "used up" in result["first_look"]["error"] and "midnight Pacific" in result["first_look"]["error"]
    listed = videos(keyed)
    assert len(listed) == 6 and sum(1 for v in listed if v["signals"]) == 2
    card = keyed.get("/api/discovery").json()["creators"][0]
    assert "used up" in card["check_error"] and card["checked_at"]
    assert units_used(keyed) == 6           # the refused request is counted too, as YouTube does


def test_one_videos_comments_can_be_read_on_request(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    row = {v["external_id"]: v for v in videos(keyed)}["stream00005"]
    jynxzi.comments["stream00005"] = [("1:02:03 😂😂", 40)]
    before = units_used(keyed)

    read = keyed.post(f"/api/discovery/videos/{row['id']}/comments").json()

    assert read["signals"]["moments"][0]["t"] == 3723.0 and units_used(keyed) == before + 1
    live = {v["external_id"]: v for v in videos(keyed)}["live0000001"]
    assert keyed.post(f"/api/discovery/videos/{live['id']}/comments").status_code == 400


# ------------------------------------------------------------------------------------- hiding, and the library
def test_a_row_whose_kept_comments_are_in_a_shape_nobody_expects_still_lists_and_is_read_again(
        keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    with db.session_scope() as s:
        s.query(CreatorVideo).filter(CreatorVideo.external_id == "stream00001").one().signals = ["not", "a", "record"]

    listed = {row["external_id"]: row for row in videos(keyed)}
    assert listed["stream00001"]["signals"] is None and len(listed) >= 5        # the page still has every video

    assert keyed.post(f"/api/discovery/creators/{creator['id']}/check").status_code == 200
    again = {row["external_id"]: row for row in videos(keyed)}
    assert again["stream00001"]["signals"]["checked"] == 5                      # and its comments were read afresh


def test_a_found_video_can_be_hidden_and_brought_back(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    row = videos(keyed)[1]

    hidden = keyed.patch(f"/api/discovery/videos/{row['id']}", json={"status": "dismissed"})

    assert hidden.status_code == 200 and hidden.json()["status"] == "dismissed"
    assert row["id"] not in [v["id"] for v in videos(keyed)]
    assert [v["id"] for v in videos(keyed, show="hidden")] == [row["id"]]
    keyed.patch(f"/api/discovery/videos/{row['id']}", json={"status": "new"})
    assert row["id"] in [v["id"] for v in videos(keyed)]
    assert keyed.patch(f"/api/discovery/videos/{row['id']}", json={"status": "gone"}).status_code == 422


def library_video(youtube_video: str, **clip_scores: float) -> int:
    """A video already in the library from that YouTube link, with clips of the given categories and scores."""
    with db.session_scope() as s:
        video = Video(title="20 WOMEN VS 1 STREAMER", original_filename="a.mp4", storage_key=storage.new_key(),
                      storage_mode="linked", file_path="C:/nowhere/a.mp4", duration=9060, width=1920, height=1080,
                      source_type="url", source_url=f"https://www.youtube.com/watch?v={youtube_video}",
                      creator_name="Jynxzi", permission_status="licensed", status="analyzed")
        s.add(video)
        s.flush()
        for index, (name, score) in enumerate(clip_scores.items()):
            s.add(Clip(video_id=video.id, storage_key=storage.new_key(), title=name, start=10.0 * index,
                       end=10.0 * index + 30, score=score, category=name.split("_")[0],
                       review_status="rejected" if name.endswith("_rejected") else "pending"))
        return video.id


def test_a_video_already_in_your_library_shows_how_its_clips_scored(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    video_id = library_video("stream00001", funny_one=84.0, funny_two=72.0, rage=70.0, funny_rejected=99.0)

    assert "stream00001" not in [v["external_id"] for v in videos(keyed)]          # no longer a suggestion
    (mine,) = videos(keyed, show="library")
    assert mine["library"] == {"video_id": video_id, "status": "analyzed", "title": "20 WOMEN VS 1 STREAMER",
                               "clips": 3, "best": 84.0, "funny": 2, "best_funny": 84.0}


def test_creators_already_in_your_library_are_suggested_until_you_follow_them(keyed: TestClient,
                                                                              jynxzi: FakeYouTubeData):
    library_video("stream00001")
    library_video("stream00002")

    (suggestion,) = keyed.get("/api/discovery").json()["suggestions"]

    assert (suggestion["name"], suggestion["videos"]) == ("Jynxzi", 2)
    found = keyed.post("/api/discovery/find", json={"text": suggestion["link"]}).json()
    follow(keyed, found["channels"][0]["id"])
    assert keyed.get("/api/discovery").json()["suggestions"] == []


# ----------------------------------------------------------------------------------------------- importing
def test_a_found_video_is_not_imported_until_you_say_why_you_may_clip_it(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    row = videos(keyed)[1]

    refused = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={})

    assert refused.status_code == 400 and "Say why you are allowed to clip this video" in refused.json()["error"]
    with db.session_scope() as s:
        assert s.query(Job).filter(Job.type == "import").count() == 0


def test_an_import_carries_the_right_you_recorded_for_the_creator(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed, permission_status="licensed", permission_note="Clipping campaign")
    row = videos(keyed)[1]

    job = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={"analyze": {"count": 4}})

    assert job.status_code == 200 and job.json()["type"] == "import"
    with db.session_scope() as s:
        payload = s.get(Job, job.json()["id"]).payload
    assert payload["url"] == "https://www.youtube.com/watch?v=stream00001"
    assert (payload["permission_status"], payload["permission_note"], payload["creator_name"]) == (
        "licensed", "Clipping campaign", "Jynxzi")
    assert payload["analyze"]["count"] == 4
    listed = {v["id"]: v for v in videos(keyed)}[row["id"]]
    assert listed["importing"]["job_id"] == job.json()["id"] and listed["importing"]["status"] == "queued"
    # asking again while it waits does not queue it twice
    again = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={})
    assert again.json()["id"] == job.json()["id"]


def test_a_right_said_for_one_video_can_be_kept_for_the_creator(keyed: TestClient, jynxzi: FakeYouTubeData):
    creator = follow(keyed)["creator"]
    first, second = videos(keyed)[1], videos(keyed)[2]

    once = keyed.post(f"/api/discovery/videos/{first['id']}/import",
                      json={"permission_status": "permission", "permission_note": "Said so on stream"})
    assert once.status_code == 200
    assert keyed.get("/api/discovery").json()["creators"][0]["permission_status"] == "unknown"   # said for one video
    kept = keyed.post(f"/api/discovery/videos/{second['id']}/import",
                      json={"permission_status": "permission", "permission_note": "Said so on stream",
                            "remember": True})

    assert kept.status_code == 200
    card = keyed.get("/api/discovery").json()["creators"][0]
    assert (card["id"], card["permission_status"], card["permission_note"]) == (
        creator["id"], "permission", "Said so on stream")


def test_a_creator_you_marked_as_not_allowed_is_never_imported_from(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed, permission_status="denied")
    row = videos(keyed)[1]

    refused = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={})
    forced = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={"permission_status": "denied"})

    assert refused.status_code == 400 and "not allowed for clipping" in refused.json()["error"]
    assert forced.status_code == 400


def test_a_right_said_for_one_video_does_not_get_around_not_allowed(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed, permission_status="denied", permission_note="Asked not to be clipped")
    row = videos(keyed)[1]

    refused = keyed.post(f"/api/discovery/videos/{row['id']}/import",
                         json={"permission_status": "licensed", "permission_note": "A campaign", "remember": True})

    assert refused.status_code == 400 and "not allowed for clipping" in refused.json()["error"]
    assert "the channel's card" in refused.json()["hint"]
    card = keyed.get("/api/discovery").json()["creators"][0]
    assert (card["permission_status"], card["permission_note"]) == ("denied", "Asked not to be clipped")
    with db.session_scope() as s:
        assert s.query(Job).filter(Job.type == "import").count() == 0      # nothing was queued


def test_a_stream_that_has_not_ended_cannot_be_imported_yet(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed, permission_status="owned")
    live = videos(keyed)[0]

    refused = keyed.post(f"/api/discovery/videos/{live['id']}/import", json={})

    assert refused.status_code == 400 and "live right now" in refused.json()["error"]


def test_a_video_already_in_your_library_is_not_imported_again(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed, permission_status="owned")
    row = videos(keyed)[1]
    library_video("stream00001")

    refused = keyed.post(f"/api/discovery/videos/{row['id']}/import", json={})

    assert refused.status_code == 409 and "already in your library" in refused.json()["error"]


# ------------------------------------------------------------------------------- the check that runs by itself
def run_next() -> Job:
    with db.session_scope() as s:
        claimed = queue.claim_next(s, "test")
        assert claimed is not None, "nothing was queued"
        job_id = claimed.id
    pipeline.run_job(job_id)
    with db.session_scope() as s:
        job = s.get(Job, job_id)
        s.expunge(job)
        return job


def test_checking_every_channel_is_one_job_with_a_line_in_the_work_log(keyed: TestClient, jynxzi: FakeYouTubeData):
    jynxzi.add_channel(TYLER, "loltyler1", "@loltyler1")
    jynxzi.add_video("tyler000001", TYLER, "Variety night", minutes=300, published=ago(2))
    follow(keyed)
    follow(keyed, TYLER)
    jynxzi.add_video("stream00006", JYNXZI, "Brand new stream", minutes=140, published=ago(hours=3))
    jynxzi.add_video("tyler000002", TYLER, "A new one", minutes=200, published=ago(hours=5))
    jynxzi.add_video("tyler000003", TYLER, "Another", minutes=90, published=ago(hours=4))

    queued = keyed.post("/api/discovery/check")
    assert queued.status_code == 200 and keyed.post("/api/discovery/check").json()["id"] == queued.json()["id"]
    job = run_next()

    assert job.status == "succeeded", job.error
    assert (job.result["channels"], job.result["new"]) == (2, 3)
    assert job.result["log"][0]["actor"] == "forge"
    assert "Checked 2 channels on YouTube: 3 new videos." in job.result["log"][0]["text"]
    assert "of 10,000 today" in job.result["log"][0]["text"]
    with db.session_scope() as s:
        notes = [(n.title, n.link) for n in s.query(Notification).order_by(Notification.id)]
    assert ("1 new video from Jynxzi", "/discovery") in notes and ("2 new videos from loltyler1", "/discovery") in notes


def test_a_refused_key_stops_the_job_and_pauses_checks_by_themselves(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    keyed.put("/api/settings/secret", json={"name": "youtube.api_key", "value": "AIza-wrong"})
    keyed.post("/api/discovery/check")

    job = run_next()

    assert job.status == "failed" and "did not accept your API key" in job.error
    assert job.result["log"][-1]["text"].startswith("Stopped: YouTube did not accept your API key")
    overview = keyed.get("/api/discovery").json()
    assert overview["paused"] and "did not accept your API key" in overview["paused"]["why"]
    with db.session_scope() as s:
        assert s.query(Notification).filter(Notification.kind == "job_failed").count() == 1
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=3)) is None        # paused
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=7)) is not None    # tried again


def test_channels_are_looked_at_by_themselves_only_when_they_are_due(keyed: TestClient, jynxzi: FakeYouTubeData):
    now = datetime.now(timezone.utc)
    with db.session_scope() as s:
        assert service.enqueue_due(s, now) is None                     # nothing is followed
    follow(keyed)
    with db.session_scope() as s:
        assert service.enqueue_due(s, now + timedelta(hours=5)) is None                # looked at five hours ago
        job = service.enqueue_due(s, now + timedelta(hours=7))                         # every six hours by default
        assert job is not None and job.type == "discover" and job.payload["all"] is False
        assert service.enqueue_due(s, now + timedelta(hours=7)).id == job.id           # not queued twice
    assert run_next().status == "succeeded"
    assert keyed.put("/api/settings", json={"values": {"discovery.check_hours": 0}}).status_code == 200
    with db.session_scope() as s:
        assert service.enqueue_due(s, now + timedelta(days=3)) is None                 # switched off


def test_asking_for_every_channel_turns_a_waiting_check_of_the_due_ones_into_a_check_of_all(
        keyed: TestClient, jynxzi: FakeYouTubeData):
    jynxzi.add_channel(TYLER, "loltyler1", "@loltyler1")
    follow(keyed)
    follow(keyed, TYLER)
    with db.session_scope() as s:                                       # queued by itself: only what is due
        waiting = service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=7))
        assert waiting.payload["all"] is False
        waiting_id = waiting.id

    pressed = keyed.post("/api/discovery/check")

    assert pressed.status_code == 200 and pressed.json()["id"] == waiting_id       # still one job, not two
    assert keyed.post("/api/discovery/check").json()["id"] == waiting_id           # and pressing again adds none
    job = run_next()
    assert job.status == "succeeded", job.error
    assert job.result["channels"] == 2             # both looked at a moment ago, so neither was due: all the same


def test_asking_for_every_channel_while_the_due_ones_are_being_checked_queues_a_check_of_all_behind_it(
        keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    with db.session_scope() as s:
        started = service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=7))
        assert queue.claim_next(s, "test").id == started.id             # it has begun, with its list made
        started_id = started.id

    pressed = keyed.post("/api/discovery/check").json()

    assert pressed["id"] != started_id
    with db.session_scope() as s:
        assert s.get(Job, pressed["id"]).payload["all"] is True and s.get(Job, started_id).payload["all"] is False
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=7)).id == started_id   # no third
        assert s.query(Job).filter(Job.type == "discover").count() == 2


def test_nothing_more_is_queued_by_itself_while_a_check_of_all_is_waiting(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    pressed = keyed.post("/api/discovery/check").json()

    with db.session_scope() as s:
        again = service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(hours=7))

        assert again.id == pressed["id"] and again.payload["all"] is True
        assert s.query(Job).filter(Job.type == "discover").count() == 1


def make_due(hours_ago: float = 7.0) -> None:
    """Every followed channel was last looked at this long ago, so it is due for a look by itself."""
    with db.session_scope() as s:
        for creator in s.query(Creator).order_by(Creator.id):
            creator.checked_at = creator.tried_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def test_an_answer_that_cannot_be_read_stays_with_its_channel_and_is_not_asked_for_again_at_once(
        keyed: TestClient, jynxzi: FakeYouTubeData):
    jynxzi.add_channel(TYLER, "loltyler1", "@loltyler1")
    follow(keyed)
    follow(keyed, TYLER)
    make_due()
    jynxzi.answer_next.append((200, {"items": [{"id": JYNXZI, "snippet": "not what the documentation describes"}]}))
    with db.session_scope() as s:
        assert service.enqueue_due(s) is not None

    job = run_next()

    assert job.status == "succeeded", job.error
    assert (job.result["channels"], job.result["failed"]) == (1, 1)        # the other channel was still looked at
    assert any("Could not check Jynxzi" in line["text"] for line in job.result["log"])
    cards = {card["title"]: card for card in keyed.get("/api/discovery").json()["creators"]}
    assert "could not be read" in cards["Jynxzi"]["check_error"] and cards["loltyler1"]["check_error"] == ""
    with db.session_scope() as s:                                          # and not again a minute later
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(minutes=5)) is None


def test_a_stored_key_this_pc_cannot_read_says_so_and_is_not_tried_again_every_minute(
        keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    make_due()
    with db.session_scope() as s:                                          # as after moving the data folder to another PC
        s.get(Secret, "youtube.api_key").ciphertext = b"saved-with-the-key-of-another-pc"
    asked = len(jynxzi.asked)

    tried = keyed.post("/api/discovery/try-key")
    with db.session_scope() as s:
        assert service.enqueue_due(s) is not None
    job = run_next()

    assert tried.status_code == 400 and "cannot be read on this PC" in tried.json()["error"]
    assert "Paste" in tried.json()["hint"]
    assert job.status == "failed" and "cannot be read on this PC" in job.error
    assert len(jynxzi.asked) == asked                                      # YouTube was never asked
    assert "cannot be read on this PC" in keyed.get("/api/discovery").json()["paused"]["why"]
    with db.session_scope() as s:
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(minutes=5)) is None


def test_after_the_data_folder_moved_to_another_pc_the_key_can_simply_be_entered_again(
        keyed: TestClient, youtube: FakeYouTubeData, data_dir, monkeypatch):  # noqa: ANN001, F811
    import base64

    from app.services import settings_store

    youtube.add_channel(service.KEY_CHECK_CHANNEL, "Google for Developers", "@GoogleDevelopers")
    (data_dir / "secret.key").write_bytes(b"DPAPI1:" + base64.b64encode(b"wrapped for another Windows account"))
    monkeypatch.setattr(settings_store, "_box", None)                      # as after starting ClipForge there

    unreadable = keyed.post("/api/discovery/try-key")
    saved = keyed.put("/api/settings/secret", json={"name": "youtube.api_key", "value": KEY})
    works = keyed.post("/api/discovery/try-key")

    assert unreadable.status_code == 400 and "cannot be read on this PC" in unreadable.json()["error"]
    assert saved.status_code == 200, saved.text
    assert works.status_code == 200 and works.json()["ok"] is True
    assert len(list(data_dir.glob("secret.key.unreadable-*"))) == 1        # the old key file is put aside, not deleted


def test_a_check_that_stops_for_a_reason_nobody_expected_is_not_started_again_a_minute_later(
        keyed: TestClient, jynxzi: FakeYouTubeData, monkeypatch):  # noqa: ANN001
    follow(keyed)
    make_due()

    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202  (a fault put in on purpose: there is no real one to use)
        raise RuntimeError("a bug nobody has found yet")

    with db.session_scope() as s:
        assert service.enqueue_due(s) is not None

    with monkeypatch.context() as fault:               # undone on leaving, and nothing else with it
        fault.setattr(service, "usage", broken)
        job = run_next()

    assert job.status == "failed"
    paused = keyed.get("/api/discovery").json()["paused"]
    assert paused and "unexpected" in paused["why"]
    with db.session_scope() as s:
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(minutes=5)) is None
    assert keyed.post(f"/api/discovery/creators/{keyed.get('/api/discovery').json()['creators'][0]['id']}/check").status_code == 200
    assert keyed.get("/api/discovery").json()["paused"] is None            # a look that works ends the pause


def test_without_a_key_nothing_is_ever_checked_by_itself(client: TestClient, jynxzi: FakeYouTubeData):
    with db.session_scope() as s:
        s.add(Creator(platform="youtube", external_id=JYNXZI, title="Jynxzi"))

    with db.session_scope() as s:
        assert service.enqueue_due(s, datetime.now(timezone.utc) + timedelta(days=1)) is None
    assert jynxzi.asked == []


# ------------------------------------------------------------------------------------------------- the rest
def test_the_quota_count_starts_again_with_youtubes_day(keyed: TestClient, jynxzi: FakeYouTubeData):
    follow(keyed)
    assert units_used(keyed) == 8

    with db.session_scope() as s:
        row = s.get(Setting, service.USAGE_KEY)
        row.value = {"day": "2020-01-01", "units": 9_999}

    assert units_used(keyed) == 0 and keyed.get("/api/discovery").json()["usage"]["limit"] == 10_000


def test_discovery_counts_as_working_only_after_youtube_really_answered(keyed: TestClient, jynxzi: FakeYouTubeData):
    def status() -> dict:
        return next(f for f in keyed.get("/api/system/features").json() if f["key"] == "discovery")

    assert status()["status"] == "needs_setup" and "Not tried against YouTube yet" in status()["detail"]
    follow(keyed)
    assert status()["status"] == "working" and "Following 1 channel" in status()["detail"]


def test_viewers_moments_reach_the_picker_only_for_a_video_found_through_discovery(keyed: TestClient,
                                                                                    jynxzi: FakeYouTubeData):
    follow(keyed)
    found_id, other_id = library_video("stream00001"), library_video("somethingelse")

    with db.session_scope() as s:
        hints = service.viewer_moments(s, s.get(Video, found_id))
        nothing = service.viewer_moments(s, s.get(Video, other_id))

    assert [(m["t"], m["likes"]) for m in hints] == [(754.0, 1230), (2465.0, 250)] and nothing == []
