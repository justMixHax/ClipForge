from __future__ import annotations

from app.transcription.sentences import Word, build_sentences


def make_words(text: str, start: float = 0.0, per_word: float = 0.3, gap: float = 0.05) -> list[Word]:
    """Evenly spaced words. A token like '<1.5>' inserts a pause of that many seconds."""
    out, t = [], start
    for token in text.split():
        if token.startswith("<") and token.endswith(">"):
            t += float(token[1:-1])
            continue
        out.append(Word(token, round(t, 3), round(t + per_word, 3), 0.95))
        t += per_word + gap
    return out


def texts(words: list[Word]) -> list[str]:
    return [s.text for s in build_sentences(words)]


def test_splits_on_sentence_punctuation():
    assert texts(make_words("No way! I got him. Did you see that? Yes")) == [
        "No way!", "I got him.", "Did you see that?", "Yes"]


def test_splits_on_a_long_pause_even_without_punctuation():
    assert texts(make_words("let me check the map <1.6> okay there is the base")) == [
        "let me check the map", "okay there is the base"]


def test_a_short_pause_does_not_split():
    assert texts(make_words("let me check <0.4> the map")) == ["let me check the map"]


def test_does_not_split_after_common_abbreviations():
    assert texts(make_words("I asked Dr. Smith about it. He said no.")) == [
        "I asked Dr. Smith about it.", "He said no."]


def test_does_not_split_inside_numbers_or_ellipsis_runs():
    assert texts(make_words("It costs 3.5 dollars. Cheap.")) == ["It costs 3.5 dollars.", "Cheap."]


def test_every_word_belongs_to_exactly_one_sentence_in_order():
    words = make_words("One two. Three four five! <2> six seven. eight")
    sentences = build_sentences(words)

    covered = []
    for s in sentences:
        covered.extend(range(s.w0, s.w1))
    assert covered == list(range(len(words)))
    assert [s.idx for s in sentences] == list(range(len(sentences)))


def test_sentence_times_come_from_its_first_and_last_word():
    words = make_words("Hello there. General greeting.", start=10.0)
    first, second = build_sentences(words)

    assert (first.start, first.end) == (words[0].start, words[1].end)
    assert (second.start, second.end) == (words[2].start, words[3].end)


def test_run_on_speech_without_punctuation_is_broken_into_readable_pieces():
    words = make_words(" ".join(f"word{i}" for i in range(95)))
    sentences = build_sentences(words)

    assert len(sentences) >= 3
    assert max(s.w1 - s.w0 for s in sentences) <= 40


def test_a_run_on_prefers_to_break_at_a_comma():
    spoken = " ".join(f"w{i}" for i in range(30)) + ", " + " ".join(f"x{i}" for i in range(25))
    sentences = build_sentences(make_words(spoken))

    assert sentences[0].text.endswith(",")


def test_empty_input_gives_no_sentences():
    assert build_sentences([]) == []


def test_whitespace_and_empty_tokens_are_ignored_in_the_text():
    words = [Word(" Hello", 0.0, 0.3, 0.9), Word("", 0.3, 0.35, 0.1), Word(" world. ", 0.4, 0.7, 0.9)]
    sentences = build_sentences(words)

    assert [s.text for s in sentences] == ["Hello world."]
    assert (sentences[0].w0, sentences[0].w1) == (0, 3)


def test_a_capitalised_word_after_a_pause_starts_a_sentence_when_punctuation_is_missing():
    """Speech models sometimes stop writing full stops but still capitalise the next sentence."""
    spoken = "Here is my hot take about this <0.6> Open world games are too big <0.5> Nobody finishes them."
    assert texts(make_words(spoken)) == [
        "Here is my hot take about this", "Open world games are too big", "Nobody finishes them."]


def test_a_name_in_the_middle_of_a_sentence_does_not_split_it():
    assert texts(make_words("and then Tyler came in <0.2> and Kai won the whole thing.")) == [
        "and then Tyler came in and Kai won the whole thing."]


def test_long_unpunctuated_speech_is_split_at_clear_pauses():
    spoken = ("no no no no no i fell off the map and then i tried again <0.7> "
              "i lost everything <0.7> three hours of progress gone")
    assert texts(make_words(spoken)) == [
        "no no no no no i fell off the map and then i tried again", "i lost everything",
        "three hours of progress gone"]


def test_a_hesitation_inside_a_punctuated_sentence_does_not_split_it():
    assert texts(make_words("It was late. I was going to the <0.7> store today.")) == [
        "It was late.", "I was going to the store today."]
