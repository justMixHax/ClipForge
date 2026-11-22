from __future__ import annotations

from app.analysis.lexicon import analyze_text, content_tokens, leading_filler_count


def test_recognises_a_question_and_an_exclamation():
    assert analyze_text("Did you see that?").question is True
    assert analyze_text("No way!").exclaim is True
    assert analyze_text("I went to the shop.").question is False


def test_hook_phrases_score_as_hooks():
    assert analyze_text("Can I tell you the dumbest thing that happened to me yesterday?").hook > 1.0
    assert analyze_text("I need some ammo and some bandages.").hook == 0


def test_humour_excitement_and_hot_takes_are_separate_signals():
    laugh = analyze_text("Ha ha ha! You were holding it the entire time?")
    hype = analyze_text("No way! Let's go! That is the craziest clutch I have ever hit!")
    take = analyze_text("Here is my hot take, and I know people will be mad about this.")

    assert laugh.humor > 0 and laugh.surprise > 0
    assert hype.excitement > 1.5 and hype.win > 0
    assert take.hot_take > 1.0 and laugh.hot_take == 0


def test_fail_and_rage_cues():
    cues = analyze_text("No, no, no, no! I fell off the map! I lost everything!")
    assert cues.fail > 1.0
    assert cues.rage > 0


def test_stream_housekeeping_is_recognised():
    assert analyze_text("So yeah, thanks for the follow, by the way.").housekeeping > 0
    assert analyze_text("Let me read some chat messages.").housekeeping > 0
    assert analyze_text("Open world games are getting way too big.").housekeeping == 0


def test_matching_respects_word_boundaries_and_case():
    assert analyze_text("She ate a lollipop in class").humor == 0       # no "lol" inside lollipop
    assert analyze_text("NO WAY").excitement > 0
    assert analyze_text("The subway was late").excitement == 0          # no "no way" inside "subway"


def test_openers_that_lean_on_earlier_context_are_flagged():
    assert analyze_text("And that's why he never came back.").dependent_start is True
    assert analyze_text("Because of that, we lost.").dependent_start is True
    assert analyze_text("I spent forty five minutes looking for my phone.").dependent_start is False


def test_references_to_earlier_context_are_counted():
    assert analyze_text("Like I said earlier, the map is huge.").context_refs >= 2
    assert analyze_text("The map is huge.").context_refs == 0


def test_numbers_and_direct_address_are_counted():
    cues = analyze_text("You spent 45 minutes and three hours on your phone")
    assert cues.numbers >= 2
    assert cues.second_person == 2


def test_leading_filler_is_counted_only_when_it_is_really_filler():
    assert leading_filler_count("Um, okay, so here is my take".split()) == 2
    assert leading_filler_count("So yeah. Um, thanks for the follow".split()) == 3
    assert leading_filler_count("Okay, watch this jump.".split()) == 1
    assert leading_filler_count("Well done everyone".split()) == 0      # "well" here carries meaning
    assert leading_filler_count("So what happened next".split()) == 0
    assert leading_filler_count("Um".split()) == 0                      # never trim a sentence to nothing


def test_content_tokens_drop_stopwords_and_punctuation():
    assert content_tokens("I spent forty five minutes looking for my phone!") == [
        "spent", "forty", "five", "minutes", "looking", "phone"]


def test_a_reply_to_someone_else_counts_as_leaning_on_earlier_context():
    assert analyze_text("No, you are wrong, and I will die on this hill.").dependent_start is True
    assert analyze_text("Yeah, but that is not the point.").dependent_start is True
    assert analyze_text("Exactly, that is what I told him.").dependent_start is True


def test_an_exclamation_is_not_mistaken_for_a_reply():
    assert analyze_text("No way! I got him!").dependent_start is False
    assert analyze_text("No! No, no, no, no! I fell off the map!").dependent_start is False
    assert analyze_text("Yes! We won the whole thing!").dependent_start is False


def test_a_question_is_recognised_without_its_question_mark_when_phrased_as_one():
    assert analyze_text("can I tell you the dumbest thing that happened").question is True
    assert analyze_text("what just happened there").question is True
    assert analyze_text("did you see that").question is True


def test_statements_are_not_mistaken_for_questions():
    assert analyze_text("I can tell you the dumbest thing").question is False
    assert analyze_text("What a shot.").question is False          # it has punctuation: trust it
    assert analyze_text("can openers are useful").question is False
