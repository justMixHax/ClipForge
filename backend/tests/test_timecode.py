"""Reading the times you type or paste: 1:02:15, 62:15, 3735, 1h2m15s, or a link shared at a moment."""
from __future__ import annotations

import pytest

from app.services.timecode import TimeError, parse_time, youtube_id


@pytest.mark.parametrize("text, seconds", [
    ("3735", 3735.0), ("3735.5", 3735.5), ("62:15", 3735.0), ("1:02:15", 3735.0), ("01:02:15.250", 3735.25),
    ("0:45", 45.0), ("1h2m15s", 3735.0), ("1h 2m 15s", 3735.0), ("2m15s", 135.0), ("45s", 45.0), ("1h", 3600.0),
    ("  1:02:15  ", 3735.0), (3735, 3735.0), (12.5, 12.5), ("1,5", 1.5),
])
def test_the_usual_ways_of_writing_a_time_are_understood(text, seconds):  # noqa: ANN001
    assert parse_time(text) == seconds


@pytest.mark.parametrize("link, seconds", [
    ("https://youtu.be/aBrr76ysyUo?t=3735", 3735.0),
    ("https://youtu.be/aBrr76ysyUo?si=Xy12&t=3735", 3735.0),
    ("https://www.youtube.com/watch?v=aBrr76ysyUo&t=3735s", 3735.0),
    ("https://www.youtube.com/watch?v=aBrr76ysyUo&t=1h2m15s", 3735.0),
    ("https://www.youtube.com/live/aBrr76ysyUo?si=abc&t=62", 62.0),
    ("https://www.youtube.com/embed/aBrr76ysyUo?start=90", 90.0),
    ("https://www.twitch.tv/videos/123456789?t=01h02m15s", 3735.0),
    ("https://www.youtube.com/watch?v=aBrr76ysyUo#t=62:15", 3735.0),
    ("youtu.be/aBrr76ysyUo?t=75", 75.0),
])
def test_a_link_shared_at_a_moment_gives_that_moment(link, seconds):  # noqa: ANN001
    assert parse_time(link) == seconds


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1:2:3:4", "1:75", "1:61:00", "-5", "12 apples", "nan", "inf",
                                 True, None, float("nan"), -3])
def test_anything_else_is_refused_with_a_message_that_shows_how_to_write_it(bad):  # noqa: ANN001
    with pytest.raises(TimeError) as caught:
        parse_time(bad)
    assert "1:02:15" in caught.value.message


def test_a_link_without_a_time_in_it_says_how_to_get_one():
    with pytest.raises(TimeError) as caught:
        parse_time("https://youtu.be/aBrr76ysyUo")
    assert "no time" in caught.value.message and "Start at" in (caught.value.hint or "")


@pytest.mark.parametrize("link, video", [
    ("https://youtu.be/aBrr76ysyUo?t=1", "aBrr76ysyUo"),
    ("https://www.youtube.com/watch?v=aBrr76ysyUo&t=3735s", "aBrr76ysyUo"),
    ("https://m.youtube.com/watch?v=aBrr76ysyUo", "aBrr76ysyUo"),
    ("https://www.youtube.com/shorts/aBrr76ysyUo", "aBrr76ysyUo"),
    ("https://www.youtube.com/live/aBrr76ysyUo?t=5", "aBrr76ysyUo"),
    ("https://www.youtube.com/embed/aBrr76ysyUo?start=90", "aBrr76ysyUo"),
    ("https://www.twitch.tv/videos/1?t=5s", None),
    ("1:02:15", None),
    ("", None),
])
def test_the_youtube_video_a_link_points_at_is_recognised(link, video):  # noqa: ANN001
    assert youtube_id(link) == video
