"""What you are looking for: the kinds of moment ClipForge can be asked to find."""
from __future__ import annotations

from typing import get_args

from app.analysis import focus
from app.analysis.scoring import CATEGORY_LABELS
from app.schemas import Focus


def test_the_kinds_asked_for_are_offered_and_best_moments_comes_first_as_the_default():
    keys = list(focus.FOCUSES)

    assert keys[0] == focus.DEFAULT == "best"
    assert {"funny", "gameplay", "horror", "rage", "streamer", "wholesome", "educational"} <= set(keys)
    assert set(get_args(Focus)) == set(keys)                # the API accepts exactly these


def test_every_kind_says_what_counts_what_proves_it_what_to_leave_out_and_how_to_open():
    for kind in focus.FOCUSES.values():
        assert kind.label and kind.hint and kind.led_by in ("words", "picture")
        assert kind.categories and set(kind.categories) <= set(CATEGORY_LABELS), kind.key
        if kind.key == "best":
            assert focus.brief(kind) == ""                  # any kind of moment: nothing to hold a pick to
            continue
        assert min(len(kind.looking_for), len(kind.proof), len(kind.not_this), len(kind.hook)) > 30, kind.key
        told = focus.brief(kind)
        assert kind.label in told and kind.looking_for in told and kind.proof in told and kind.not_this in told


def test_an_unknown_kind_is_read_as_best_moments():
    assert focus.get("funny").key == "funny"
    assert focus.get("nonsense").key == "best" and focus.get(None).key == "best" and focus.get("").key == "best"


def test_the_local_rules_lift_moments_of_the_kind_asked_for_and_lower_the_others():
    funny = focus.get("funny")

    assert focus.local_score(70, "funny", funny) == 70 + focus.LOCAL_BONUS
    assert focus.local_score(70, "debate", funny) == 70 - focus.LOCAL_PENALTY
    assert focus.local_score(70, "debate", focus.get("best")) == 70          # nothing asked for: nothing changes
    assert focus.local_score(98, "funny", funny) == 100 and focus.local_score(3, "debate", funny) == 0
