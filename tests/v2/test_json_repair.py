"""tests/v2/test_json_repair.py — the LLM output defects that actually occur.

The writer's parser was strict `json.loads`. Against the real gateway
(tig/step-5-unrestricted) it rejected every single draft: the model emits

    {
     "content": "...."
     "disclosure":
    }

a key with **no value at all**. The gate then correctly refused to publish
anything, which meant the writer produced zero drafts against the real model
while every unit test passed, because the tests fed it clean JSON.

These tests use the *actual* malformed shapes observed, not invented ones.
"""
from __future__ import annotations

import json

import pytest

from src.content.writer import _parse_draft_payload, _repair_json
from src.core.llm import LLMError


# ── The exact defect observed against the live gateway ──────────────────────
OBSERVED_EMPTY_KEY = '''{
 "content": "Kamu nggak akan pernah sadar kenapa kabel charger di tas selalu kusut sebelum ujungnya belepotan."
 "disclosure":
}'''

OBSERVED_EMPTY_KEY_THREAD = '''{
 "parts": ["Bagian satu.", "Bagian dua."]
 "bonus_reply":
}'''


class TestObservedDefects:
    def test_strict_parse_rejects_the_observed_shape(self):
        """Prove the defect is real before proving the fix works."""
        with pytest.raises(json.JSONDecodeError):
            json.loads(OBSERVED_EMPTY_KEY)

    def test_repair_fixes_the_observed_shape(self):
        data = json.loads(_repair_json(OBSERVED_EMPTY_KEY))
        assert data["content"].startswith("Kamu nggak akan pernah sadar")
        assert data["disclosure"] == ""

    def test_parse_payload_accepts_the_observed_shape(self):
        content, parts, disclosure = _parse_draft_payload(OBSERVED_EMPTY_KEY)
        assert content.startswith("Kamu nggak akan pernah sadar")
        assert parts == []
        assert disclosure == ""

    def test_repair_fixes_thread_variant(self):
        data = json.loads(_repair_json(OBSERVED_EMPTY_KEY_THREAD))
        assert data["parts"] == ["Bagian satu.", "Bagian dua."]
        assert data["bonus_reply"] == ""

    def test_parse_payload_accepts_thread_variant(self):
        content, parts, _ = _parse_draft_payload(OBSERVED_EMPTY_KEY_THREAD)
        assert parts == ["Bagian satu.", "Bagian dua."]
        assert content == "Bagian satu.\n\nBagian dua."


class TestOtherRealDefects:
    def test_missing_comma_between_members(self):
        raw = '{\n "content": "satu"\n "disclosure": ""\n}'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "satu"

    def test_smart_quotes_inside_content_are_preserved(self):
        """A smart quote inside a string is CONTENT, not a delimiter.

        Converting it would silently alter the model's words. Only quotes that
        structurally open or close a value are normalised.
        """
        raw = '{\n "content": "\u201chalooo\u201d",\n "disclosure": ""\n}'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "\u201chalooo\u201d"

    def test_structural_smart_quotes_are_normalised(self):
        """A smart quote wrapping the whole value IS a delimiter."""
        raw = '{\n "content": \u201chalooo\u201d,\n "disclosure": ""\n}'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "halooo"

    def test_trailing_comma(self):
        raw = '{"content": "satu", "disclosure": "",}'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "satu"

    def test_markdown_fence_despite_instructions(self):
        raw = '```json\n{"content": "satu", "disclosure": ""}\n```'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "satu"

    def test_bare_fence(self):
        raw = '```\n{"content": "satu"}\n```'
        content, _, _ = _parse_draft_payload(raw)
        assert content == "satu"


class TestRepairNeverInvetsContent:
    """The repair must only supply empty values, never fabricate words."""

    def test_content_is_byte_identical_after_repair(self):
        original = "Kamu nggak akan pernah sadar kenapa kabel charger di tas selalu kusut."
        raw = '{\n "content": "%s"\n "disclosure":\n}' % original
        content, _, _ = _parse_draft_payload(raw)
        assert content == original

    def test_repair_does_not_add_words(self):
        raw = '{\n "content": "satu dua tiga"\n "disclosure":\n}'
        content, _, _ = _parse_draft_payload(raw)
        assert content.split() == ["satu", "dua", "tiga"]

    def test_empty_key_becomes_empty_string(self):
        data = json.loads(_repair_json('{"content": "x", "disclosure":}'))
        assert data["disclosure"] == ""

    def test_repair_is_idempotent(self):
        once = _repair_json(OBSERVED_EMPTY_KEY)
        twice = _repair_json(once)
        assert json.loads(once) == json.loads(twice)


class TestStillFailsClosed:
    """Repair must not become a licence to accept garbage."""

    def test_prose_is_still_rejected(self):
        with pytest.raises(LLMError):
            _parse_draft_payload("this is not json at all")

    def test_json_array_is_rejected(self):
        with pytest.raises(LLMError) as ei:
            _parse_draft_payload('["a", "b"]')
        assert "not an object" in str(ei.value)

    def test_empty_content_is_rejected(self):
        with pytest.raises(LLMError) as ei:
            _parse_draft_payload('{"content": "", "disclosure": ""}')
        assert "empty" in str(ei.value)

    def test_whitespace_content_is_rejected(self):
        with pytest.raises(LLMError):
            _parse_draft_payload('{"content": "   ", "disclosure": ""}')

    def test_empty_parts_are_rejected(self):
        with pytest.raises(LLMError) as ei:
            _parse_draft_payload('{"parts": [], "disclosure": ""}')
        assert "no usable parts" in str(ei.value)

    def test_blank_parts_are_rejected(self):
        with pytest.raises(LLMError):
            _parse_draft_payload('{"parts": ["", "  "], "disclosure": ""}')

    def test_unrepairable_garbage_is_rejected(self):
        with pytest.raises(LLMError):
            _parse_draft_payload('{"content": "x" "disclosure": ')

    def test_truncated_json_is_rejected(self):
        with pytest.raises(LLMError):
            _parse_draft_payload('{"content": "potongan di')

    def test_repair_returns_input_unchanged_when_hopeless(self):
        raw = "total nonsense"
        assert _repair_json(raw) == raw


class TestCleanJsonStillWorks:
    """The repair must not regress the well-formed path."""

    def test_clean_single(self):
        content, parts, disclosure = _parse_draft_payload(
            '{"content": "halo", "disclosure": ""}'
        )
        assert content == "halo"
        assert parts == []

    def test_clean_thread(self):
        content, parts, _ = _parse_draft_payload(
            '{"parts": ["a", "b"], "disclosure": "link afiliasi"}'
        )
        assert parts == ["a", "b"]
        assert content == "a\n\nb"

    def test_disclosure_is_preserved(self):
        _, _, disclosure = _parse_draft_payload(
            '{"content": "x", "disclosure": "Post ini mengandung link afiliasi."}'
        )
        assert "afiliasi" in disclosure

    def test_unicode_content_survives(self):
        content, _, _ = _parse_draft_payload(
            '{"content": "café — naïve — 日本語", "disclosure": ""}'
        )
        assert content == "café — naïve — 日本語"
