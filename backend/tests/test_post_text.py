"""The text that goes with a clip when it is posted: its hashtags and the caption to paste into TikTok."""
from __future__ import annotations

from app.analysis.describe import credit_line, merge_hashtags, post_caption, strip_emoji


def test_the_clips_own_hashtags_come_first_then_the_usual_ones_each_once_and_six_at_most():
    assert merge_hashtags(["#lostphone", "#GTA6"], ["#gta6", "#kai", "#funny", "#gaming", "#fyp", "#viral"]) == [
        "#lostphone", "#GTA6", "#kai", "#funny", "#gaming", "#fyp"]


def test_the_tiktok_caption_is_the_words_then_the_credit_then_the_hashtags():
    assert post_caption("He looked for 45 minutes.", "Clip from Kai", ["#a1", "#b2"]) == (
        "He looked for 45 minutes.\nClip from Kai\n\n#a1 #b2")
    assert post_caption("Title\n\nClip from Kai", "Clip from Kai", []) == "Title\n\nClip from Kai"  # credit already in
    assert post_caption("Title", "", []) == "Title"


def test_the_credit_is_your_own_wording_when_you_gave_one():
    assert credit_line("", "Kai") == "Clip from Kai"
    assert credit_line("Thanks to @kai for the stream", "Kai") == "Thanks to @kai for the stream"
    assert credit_line("", "") == ""


def test_emojis_can_be_taken_out_of_a_caption_and_the_rest_stays():
    assert strip_emoji("Wait for it 😂🔥 he did it! 👀") == "Wait for it he did it!"
    assert strip_emoji("Thumbs 👍🏽 up ❤️ yes\nsecond line 🇺🇸 here") == "Thumbs up yes\nsecond line here"
    assert strip_emoji("Season 2, 100% real: $250") == "Season 2, 100% real: $250"


def test_the_hashtags_are_specific_first_and_never_a_general_one_like_fyp():
    # A general tag (#fyp, #viral) sends the clip to everyone; specific tags send it to people who would watch it.
    from app.analysis.describe import build_hashtags

    tags = build_hashtags("funny", "gaming", "gta", "Kai Cenat", "")

    assert tags == ["#gta", "#kaicenat", "#funny", "#gaming", "#gamingclips"]
    assert not any(t.lower() in ("#fyp", "#foryou", "#foryoupage", "#viral") for t in tags)


def test_the_credit_names_the_creators_handle_when_it_is_known():
    # Tagging the creator's own account reaches the people who follow them.
    assert credit_line("", "Kai Cenat", "@KaiCenat") == "Clip from @KaiCenat"
    assert credit_line("", "Kai Cenat", "KaiCenat") == "Clip from @KaiCenat"
    assert credit_line("", "Kai Cenat", "") == "Clip from Kai Cenat"
    assert credit_line("Thanks to Kai", "Kai Cenat", "@KaiCenat") == "Thanks to Kai"   # your wording still wins
