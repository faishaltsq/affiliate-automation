import pytest
from src.clipper.subtitles import format_ass_timestamp, wrap_word_timed_subtitles

def test_wrap_word_timed_subtitles():
    class DummyWord:
        def __init__(self, word, start, end):
            self.word = word
            self.start = start
            self.end = end

    words = [
        DummyWord("Ini", 1.0, 1.3),
        DummyWord("surat", 1.4, 1.8),
        DummyWord("emasnya", 1.9, 2.4),
        DummyWord("bermasalah", 2.5, 3.1),
        DummyWord("sekali", 3.2, 3.6),
    ]
    chunks = wrap_word_timed_subtitles(words, max_words=3)
    assert len(chunks) == 2
    assert chunks[0]["text"] == "Ini surat emasnya"
    assert chunks[0]["start"] == 1.0
    assert chunks[0]["end"] == 2.4
    assert chunks[1]["text"] == "bermasalah sekali"
    assert chunks[1]["start"] == 2.5
    assert chunks[1]["end"] == 3.6
