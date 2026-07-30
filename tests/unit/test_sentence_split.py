"""Unit tests for economy sentence segmentation."""

from __future__ import annotations

from src.engines.pipeline.sentence_split import (
    ensure_terminal_punct,
    is_punct_only,
    split_sentences,
)


def test_split_zh_sentences():
    parts = split_sentences("你好世界。今天天气不错！真的吗？")
    assert parts == ["你好世界。", "今天天气不错！", "真的吗？"]


def test_split_en_sentences():
    parts = split_sentences("Hello world. How are you? Fine.")
    assert parts == ["Hello world.", "How are you?", "Fine."]


def test_split_merges_tiny_fragments():
    parts = split_sentences("Hi. A. Hello there.", min_chars=4)
    assert parts == ["Hi.", "A. Hello there."]


def test_split_preserves_decimal_and_abbreviation():
    parts = split_sentences("Dr. Smith measured 3.14 volts. It worked.")
    assert parts == ["Dr. Smith measured 3.14 volts.", "It worked."]


def test_split_keeps_closing_quote_with_sentence():
    parts = split_sentences('He said "Ready?" Then we left.')
    assert parts == ['He said "Ready?"', "Then we left."]


def test_split_no_punct_keeps_whole():
    assert split_sentences("no punctuation here") == ["no punctuation here"]


def test_split_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_newlines():
    parts = split_sentences("第一句\n第二句")
    assert parts == ["第一句", "第二句"]


def test_ensure_terminal_punct_zh():
    assert ensure_terminal_punct("你好") == "你好。"
    assert ensure_terminal_punct("你好。") == "你好。"


def test_ensure_terminal_punct_en():
    assert ensure_terminal_punct("Hello") == "Hello."
    assert ensure_terminal_punct("Hello!") == "Hello!"


def test_is_punct_only():
    assert is_punct_only("...")
    assert is_punct_only("！！")
    assert is_punct_only("")
    assert not is_punct_only("hi")
    assert not is_punct_only("你好")
