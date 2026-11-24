"""What ClipForge reads out of YouTube's public data about a video before it is downloaded: how many of its top
comments are laughing, which moments viewers point at, and whether it stands out among the channel's videos.
Nothing here is a prediction: every number is counted from what YouTube returned."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.discovery.signals import (channel_query, clean_excerpt, comment_signals, comment_times, is_laughing,
                                   iso_duration, pacific_day, parse_when, pointed_moments, standout)


def comment(text: str, likes: int = 0) -> dict:
    return {"text": text, "likes": likes}


# ----------------------------------------------------------------------------------- what a channel is called
@pytest.mark.parametrize("text, kind, value", [
    ("UCjiXtODGCCulmhwypZAWSag", "id", "UCjiXtODGCCulmhwypZAWSag"),
    ("https://www.youtube.com/channel/UCjiXtODGCCulmhwypZAWSag/videos", "id", "UCjiXtODGCCulmhwypZAWSag"),
    ("@jynxzi", "handle", "@jynxzi"),
    ("https://www.youtube.com/@Jynxzi", "handle", "@Jynxzi"),
    ("youtube.com/@Jynxzi/streams", "handle", "@Jynxzi"),
    ("https://m.youtube.com/@%EC%B9%A8%EC%B0%A9%EB%A7%A8", "handle", "@침착맨"),          # handles in any script
    ("https://www.youtube.com/user/loltyler1", "username", "loltyler1"),
    ("https://www.youtube.com/c/SomeCreator", "custom", "SomeCreator"),
    ("https://www.youtube.com/SomeCreator", "custom", "SomeCreator"),
    ("https://www.youtube.com/watch?v=lbkzsFyT7gA", "video", "lbkzsFyT7gA"),
    ("https://youtu.be/lbkzsFyT7gA?t=90", "video", "lbkzsFyT7gA"),
    ("https://www.youtube.com/live/lbkzsFyT7gA", "video", "lbkzsFyT7gA"),
    ("jynxzi funny moments", "words", "jynxzi funny moments"),
    ("  Tyler1  ", "words", "Tyler1"),
])
def test_what_you_type_is_read_as_a_channel_a_video_or_words_to_search(text: str, kind: str, value: str):
    assert channel_query(text) == (kind, value)


@pytest.mark.parametrize("text", ["https://www.twitch.tv/jynxzi", "https://kick.com/someone",
                                  "https://www.youtube.com/feed/subscriptions", "https://www.youtube.com/results?q=x"])
def test_links_that_are_not_a_youtube_channel_or_video_are_named_as_such(text: str):
    assert channel_query(text)[0] == "other"


def test_nothing_typed_is_nothing():
    assert channel_query("   ") == ("empty", "")


# ----------------------------------------------------------------------------------------- lengths and dates
@pytest.mark.parametrize("text, seconds", [("PT1H2M10S", 3730), ("PT45S", 45), ("PT11H41M52S", 42112), ("P1DT2H", 93600),
                                           ("PT3M", 180), ("P0D", 0), ("", 0), (None, 0), ("nonsense", 0)])
def test_youtubes_lengths_are_read_as_seconds(text, seconds: int):  # noqa: ANN001
    assert iso_duration(text) == seconds


def test_youtubes_dates_are_read_as_utc():
    assert parse_when("2026-11-20T18:00:05Z") == datetime(2026, 11, 20, 18, 0, 5, tzinfo=timezone.utc)
    assert parse_when("2026-11-20T18:00:05.123+01:00") == datetime(2026, 11, 20, 17, 0, 5, 123000, tzinfo=timezone.utc)
    assert parse_when("") is None and parse_when("yesterday") is None and parse_when(None) is None


@pytest.mark.parametrize("utc, day", [
    (datetime(2026, 11, 24, 7, 59, tzinfo=timezone.utc), "2026-11-23"),     # 23:59 Pacific Standard Time
    (datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc), "2026-11-24"),      # midnight PST: the quota is new
    (datetime(2026, 7, 4, 6, 59, tzinfo=timezone.utc), "2026-07-03"),       # 23:59 Pacific Daylight Time
    (datetime(2026, 7, 4, 7, 0, tzinfo=timezone.utc), "2026-07-04"),
    (datetime(2026, 3, 8, 9, 59, tzinfo=timezone.utc), "2026-03-08"),       # 01:59 PST on the day the clocks change
    (datetime(2026, 11, 1, 8, 59, tzinfo=timezone.utc), "2026-11-01"),      # 01:59 PDT on the day they change back
])
def test_the_quota_day_follows_pacific_time_through_the_year(utc: datetime, day: str):
    assert pacific_day(utc) == day


# ----------------------------------------------------------------------------------------------- laughter
@pytest.mark.parametrize("text", ["LMAOOO he really said that", "😂😂😂", "bro 💀", "I'm crying 😭", "hahaha what",
                                  "this is the funniest stream ever", "lol", "LOL.", "im dead", "I'm dying at 3:20",
                                  "jajaja", "ㅋㅋㅋㅋ", "absolutely hilarious", "AHAHAHA", "I can't stop laughing"])
def test_comments_that_laugh_are_counted_as_laughing(text: str):
    assert is_laughing(text)


@pytest.mark.parametrize("text", ["great video", "first", "what game is this?", "lolita is a book", "my battery is dead",
                                  "he died at the end", "halo was better", "the funnel was broken", ""])
def test_comments_that_do_not_laugh_are_not(text: str):
    assert not is_laughing(text)


# ------------------------------------------------------------------------------------- moments in comments
def test_times_written_in_a_comment_are_read():
    assert comment_times("12:34 bro fell off the chair 😂", 3600) == [754.0]
    assert comment_times("the part at 1:02:15 and again 1:05:00", 7200) == [3735.0, 3900.0]
    assert comment_times("75:30 is wild", 7200) == [4530.0]


@pytest.mark.parametrize("text", ["it ended 3:2", "ratio 16:9 looks better", "see you at 10:30 pm", "v1.2:30",
                                  "99:99 lol", "", "john 3:16"])
def test_things_that_only_look_like_a_time_are_left_out(text: str):
    # a time must fit inside the video: this one is 3 minutes long
    assert comment_times(text, 180) == []


def test_a_time_past_the_end_of_the_video_is_not_a_moment():
    assert comment_times("45:10 😂", 600) == [] and comment_times("45:10 😂", 3000) == [2710.0]


def test_a_list_of_chapters_is_not_six_funny_moments():
    chapters = "0:00 intro 2:10 ranked 10:05 clutch 15:30 rage 21:00 scam call 30:12 outro"

    assert pointed_moments([comment(chapters, 900)], 3600) == []


def test_comments_about_the_same_moment_are_counted_together_most_liked_first():
    comments = [comment("12:34 he fell off the chair 😂", 1200), comment("LMAO 12:40", 30), comment("12:31", 4),
                comment("the scam call at 41:05 is gold", 250), comment("no time here", 9000),
                comment("59:59 ending", 1)]

    moments = pointed_moments(comments, 3600)

    assert [(m["t"], m["mentions"], m["likes"]) for m in moments] == [(754.0, 3, 1234), (2465.0, 1, 250), (3599.0, 1, 1)]
    assert moments[0]["text"] == "12:34 he fell off the chair 😂" and moments[0]["laughing"] is True
    assert moments[1]["laughing"] is False


def test_only_the_most_pointed_at_moments_are_kept():
    comments = [comment(f"{minute}:00 nice", minute) for minute in range(1, 30)]

    moments = pointed_moments(comments, 3600, limit=5)

    assert [m["t"] for m in moments] == [1740.0, 1680.0, 1620.0, 1560.0, 1500.0]


def test_a_comment_is_shortened_and_cleaned_for_showing():
    assert clean_excerpt("  line one\n\nline   two\t!  ") == "line one line two !"
    assert clean_excerpt("x" * 300) == "x" * 139 + "…"
    assert clean_excerpt("bad\x00\x07chars") == "badchars"


def test_the_signals_of_a_video_are_plain_counts():
    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    comments = [comment("12:34 😂", 10), comment("lol"), comment("good stream"), comment("what a game")]

    signals = comment_signals(comments, 3600, now)

    assert (signals["checked"], signals["laughing"]) == (4, 2)
    assert signals["moments"][0]["t"] == 754.0 and signals["fetched_at"] == "2026-11-24T12:00:00+00:00"
    assert comment_signals([], 3600, now) == {"checked": 0, "laughing": 0, "moments": [],
                                              "fetched_at": "2026-11-24T12:00:00+00:00"}


# --------------------------------------------------------------------------------------------- standing out
def test_a_video_stands_out_by_its_views_against_the_channels_usual():
    usual = [100_000, 120_000, 90_000, 110_000, 95_000]

    assert standout(330_000, usual) == 3.3
    assert standout(50_000, usual) == 0.5


def test_too_little_to_compare_with_gives_no_number():
    assert standout(330_000, [100_000, 120_000, 90_000]) is None       # fewer than four other videos
    assert standout(None, [1, 2, 3, 4, 5]) is None                     # views hidden
    assert standout(10, [0, 0, 0, 0, 0]) is None                       # nothing to divide by
