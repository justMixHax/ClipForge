"""The YouTube Data API client: what it asks for, how it reads the answers, how it counts the quota, and what it
says when YouTube refuses. It talks to a local stand-in; the real API is never called from the tests."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.discovery.youtube_api import DiscoveryError, YouTubeData
from tests.fake_youtube_data import KEY, FakeYouTubeData, youtube  # noqa: F401  (fixture)

JYNXZI = "UCjiXtODGCCulmhwypZAWSag"
TYLER = "UCwV_0HmQkRrTcrReaMxPeDw"


@pytest.fixture()
def api(youtube: FakeYouTubeData) -> YouTubeData:  # noqa: F811
    youtube.add_channel(JYNXZI, "Jynxzi", "@Jynxzi", subscribers=6_500_000, description="Rainbow  Six\nSiege streams")
    youtube.add_channel(TYLER, "loltyler1", "@loltyler1", subscribers=None, username="loltyler1")
    youtube.add_video("vid00000001", JYNXZI, "20 WOMEN VS 1 STREAMER", minutes=151.15, views=3_100_000, likes=91_000,
                      comments=4_200, published="2026-11-18T20:00:00Z", was_live=True)
    youtube.add_video("vid00000002", JYNXZI, "Short clip", minutes=0.5, views=900_000, likes=None, comments=None)
    client = YouTubeData(KEY)
    yield client
    client.close()


def test_a_test_that_forgets_the_stand_in_cannot_reach_the_real_youtube():
    """conftest.py points the client at an address on this PC where nothing listens. A test without the stand-in
    (or one that lost it) then fails, instead of asking Google with whatever key it holds."""
    client = YouTubeData("AIza-a-test-key")
    try:
        assert client.base.startswith("http://127.0.0.1:"), client.base
        with pytest.raises(DiscoveryError) as refused:
            client.channels(ids=[JYNXZI])
        assert refused.value.retry and "could not be reached" in refused.value.message
    finally:
        client.close()


# ------------------------------------------------------------------------------------------------- the key
def test_the_key_travels_in_a_header_and_never_in_the_address(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    api.channels(ids=[JYNXZI])
    api.videos(["vid00000001"])
    api.top_comments("vid00000001")

    assert len(youtube.asked) == 3
    for asked in youtube.asked:
        assert asked.headers["x-goog-api-key"] == KEY
        assert "key" not in asked.query and KEY not in str(asked.query)


def test_a_key_youtube_does_not_accept_says_so_and_what_to_do(youtube: FakeYouTubeData):  # noqa: F811
    with pytest.raises(DiscoveryError) as caught:
        YouTubeData("AIza-wrong").channels(ids=[JYNXZI])

    assert caught.value.key and "did not accept your API key" in caught.value.message
    assert "Credentials" in caught.value.hint and "AIza-wrong" not in caught.value.message + caught.value.hint


# ------------------------------------------------------------------------------------------------ channels
def test_a_channel_is_read_with_its_public_numbers(api: YouTubeData):
    (channel,) = api.channels(ids=[JYNXZI])

    assert channel == {"id": JYNXZI, "title": "Jynxzi", "handle": "@jynxzi", "url": "https://www.youtube.com/@jynxzi",
                       "description": "Rainbow Six Siege streams", "thumb_url": f"https://yt3.ggpht.com/{JYNXZI}=s240",
                       "subscribers": 6_500_000, "video_count": 2, "uploads": "UU" + JYNXZI[2:]}


def test_a_hidden_subscriber_count_stays_unknown(api: YouTubeData):
    assert api.channels(ids=[TYLER])[0]["subscribers"] is None


def test_a_channel_is_found_by_its_handle_or_its_old_user_name(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    assert [c["id"] for c in api.channels(handle="@Jynxzi")] == [JYNXZI]
    assert [c["id"] for c in api.channels(username="loltyler1")] == [TYLER]
    assert api.channels(handle="@nobody-here") == [] and api.channels(ids=["UC" + "x" * 22]) == []
    assert youtube.calls("channels")[0].query["forHandle"] == "@Jynxzi"


def test_channels_come_back_in_the_order_asked_for(api: YouTubeData):
    assert [c["id"] for c in api.channels(ids=[TYLER, JYNXZI, TYLER])] == [TYLER, JYNXZI]


def test_searching_by_name_costs_a_hundred_units_and_says_so_in_the_count(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    found = api.search_channels("jynx")

    assert [c["title"] for c in found] == ["Jynxzi"] and found[0]["subscribers"] == 6_500_000
    assert api.spent == 101 == youtube.units()                     # the search, then one request for the numbers
    assert api.search_channels("nothing like this") == [] and api.spent == 201


# -------------------------------------------------------------------------------------------------- videos
def test_the_newest_uploads_are_listed_newest_first(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    assert api.uploads("UU" + JYNXZI[2:], limit=25) == ["vid00000002", "vid00000001"]
    assert youtube.calls("playlistItems")[0].query["maxResults"] == "25"
    assert api.uploads("UU" + TYLER[2:]) == []                     # a channel with nothing uploaded
    assert api.uploads("UUnothing") == []                          # YouTube says "not found" for those


def test_a_video_is_read_with_its_length_date_and_public_numbers(api: YouTubeData):
    (stream, clip) = api.videos(["vid00000001", "vid00000002", "gone0000000"])

    assert stream == {"id": "vid00000001", "channel_id": JYNXZI, "title": "20 WOMEN VS 1 STREAMER", "description": "",
                      "published_at": datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc), "duration": 9069.0,
                      "live": "none", "was_live": True, "views": 3_100_000, "likes": 91_000, "comments": 4_200,
                      "thumb_url": "https://i.ytimg.com/vi/vid00000001/mqdefault.jpg"}
    assert (clip["duration"], clip["likes"], clip["comments"], clip["was_live"]) == (30.0, None, None, False)


def test_a_stream_that_is_still_live_has_no_length_yet(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.add_video("live0000001", JYNXZI, "LIVE NOW", live="live")

    (video,) = api.videos(["live0000001"])

    assert (video["live"], video["duration"]) == ("live", 0.0)


def test_many_videos_are_asked_for_fifty_at_a_time(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    ids = [youtube.add_video(f"many{n:07d}", JYNXZI, f"Video {n}") for n in range(120)]

    assert len(api.videos(ids)) == 120
    assert [len(a.query["id"].split(",")) for a in youtube.calls("videos")] == [50, 50, 20] and api.spent == 3


# ------------------------------------------------------------------------------------------------ comments
def test_top_comments_come_with_their_likes(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.comments["vid00000001"] = [("12:34 he fell off the chair 😂", 1200), ("  ", 5), ("W stream", 3)]

    assert api.top_comments("vid00000001") == [{"text": "12:34 he fell off the chair 😂", "likes": 1200},
                                               {"text": "W stream", "likes": 3}]
    asked = youtube.calls("commentThreads")[0].query
    assert (asked["order"], asked["maxResults"], asked["textFormat"]) == ("relevance", "100", "plainText")


def test_comments_switched_off_is_not_an_error(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.comments_off.add("vid00000001")

    assert api.top_comments("vid00000001") is None and api.top_comments("gone0000000") is None
    assert api.top_comments("vid00000002") == []


# ------------------------------------------------------------------------------------------- when it refuses
def test_a_used_up_quota_says_when_it_starts_again(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.units_left = 1
    api.channels(ids=[JYNXZI])

    with pytest.raises(DiscoveryError) as caught:
        api.channels(ids=[JYNXZI])

    assert caught.value.quota and "used up" in caught.value.message and "midnight Pacific" in caught.value.hint
    assert "<a" not in caught.value.message and api.spent == 2      # a refused request is counted too


def test_an_api_that_is_not_switched_on_says_where_to_switch_it_on(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.disabled = True

    with pytest.raises(DiscoveryError) as caught:
        api.channels(ids=[JYNXZI])

    assert caught.value.key and "not switched on" in caught.value.message and "Enable" in caught.value.hint


def test_a_restricted_key_says_which_setting_to_change(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.restricted = True

    with pytest.raises(DiscoveryError) as caught:
        api.channels(ids=[JYNXZI])

    assert caught.value.key and "restrictions" in caught.value.message and "API restrictions" in caught.value.hint


def test_no_key_at_all_is_refused_in_youtubes_own_words(youtube: FakeYouTubeData):  # noqa: F811
    with pytest.raises(DiscoveryError) as caught:
        YouTubeData("").channels(ids=[JYNXZI])

    assert caught.value.key and "Permission denied on the service" in caught.value.message


def test_a_problem_on_youtubes_side_can_be_tried_again(api: YouTubeData, youtube: FakeYouTubeData):  # noqa: F811
    youtube.answer_next = [503, 429]

    with pytest.raises(DiscoveryError) as first:
        api.channels(ids=[JYNXZI])
    with pytest.raises(DiscoveryError) as second:
        api.channels(ids=[JYNXZI])

    assert first.value.retry and "problem on its side (503)" in first.value.message
    assert second.value.retry and "too many requests" in second.value.message
    assert [c["id"] for c in api.channels(ids=[JYNXZI])] == [JYNXZI]


def test_no_connection_is_said_plainly(youtube: FakeYouTubeData):  # noqa: F811
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = YouTubeData(KEY, client=httpx.Client(transport=httpx.MockTransport(refuse)))

    with pytest.raises(DiscoveryError) as caught:
        client.channels(ids=[JYNXZI])

    assert caught.value.retry and "could not be reached" in caught.value.message
    assert "internet connection" in caught.value.hint
