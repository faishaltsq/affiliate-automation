"""tests/test_chain_pipeline_numbers.py — a chain may not invent its figures.

The chain prompt tells the model to take numbers from the source, but for a
long time the model was handed nothing but a headline, so it filled the gap
with invented percentages. These tests pin the two layers that close it: the
feed description that reaches the prompt, and the post-generation guard that
strips any figure the source never stated.
"""

import pytest

from src.organic.generator import (
    _figure_tokens,
    _sourced_tokens,
    _strip_unsourced_numbers,
)

TOPIC = "AI Akan Ubah Industri Otomotif, Desainer Mobil Terancam Kena PHK?"
SUMMARY_WITH_FIGURES = (
    "Manufaktur otomotif 2026 adopsi AI melonjak, lowongan desainer junior "
    "anjlok 40%, pangkas fase visualisasi konsep 70%."
)


# ── Figure extraction ────────────────────────────────────────────────────────

def test_percentages_are_extracted():
    assert _figure_tokens("Lowongan desainer anjlok 40%") == {"40"}


def test_percent_word_variant_matches_percentage_digit():
    # "40 persen" and "40%" are the same claim and must land on one token.
    assert _figure_tokens("anjlok 40 persen") == _figure_tokens("anjlok 40%")


def test_thousands_separator_is_one_figure_not_two():
    assert _figure_tokens("IHSG ditutup di 7.100") == {"7100"}


def test_product_designations_are_not_figures():
    # "3D", "4K", "5G" are names, not statistics. Treating them as figures
    # would strip them and leave "modeling D" in published copy.
    assert "3" not in _figure_tokens("generative modeling 3D")
    assert "5" not in _figure_tokens("jaringan 5G")


def test_bare_year_is_not_a_figure():
    # The chain's comment trigger is a binary prediction against a year — the
    # whole structure depends on that question surviving.
    assert _figure_tokens("sebelum atau sesudah 2028?") == set()


def test_empty_text_yields_no_figures():
    assert _figure_tokens("") == set()


# ── What the source allows ───────────────────────────────────────────────────

def test_summary_figures_are_allowed():
    assert _sourced_tokens(TOPIC, SUMMARY_WITH_FIGURES) == {"40", "70"}


def test_no_summary_means_no_allowed_figures():
    assert _sourced_tokens(TOPIC, "") == set()


def test_topic_figures_are_allowed():
    # A headline that carries its own figure must not be stripped from copy.
    # "5.100" normalises to the digits only, so both sides drop separators.
    assert "5100" in _sourced_tokens("IHSG 5.100 ditutup melemah", "")
    assert _strip_unsourced_numbers(
        "", ["IHSG ditutup di 5.100."], "", "IHSG 5.100 ditutup melemah", ""
    )[3] == set()


# ── The post-generation guard ────────────────────────────────────────────────

def test_unsourced_figures_are_flagged():
    _, parts, _, fabricated = _strip_unsourced_numbers(
        "", ["Lowongan desainer anjlok 40% tapi pangkas konsep 70%."], "", TOPIC, ""
    )
    assert fabricated == {"40", "70"}
    assert parts == ["Lowongan desainer anjlok tapi pangkas konsep."]


def test_sourced_figures_are_kept_verbatim():
    content, parts, bonus, fabricated = _strip_unsourced_numbers(
        "", ["Lowongan desainer anjlok 40%."], "", TOPIC, SUMMARY_WITH_FIGURES
    )
    assert fabricated == set()
    assert parts == ["Lowongan desainer anjlok 40%."]
    assert content == ""
    assert bonus == ""


def test_trigger_year_survives_the_guard():
    # Removing the year would destroy the comment trigger the chain is built on.
    _, parts, _, fabricated = _strip_unsourced_numbers(
        "", ["Divisi desain bakal bubar sebelum atau sesudah 2028?"], "", TOPIC, ""
    )
    assert fabricated == set()
    assert parts[0].endswith("2028?")


def test_product_designation_survives_the_guard():
    _, parts, _, fabricated = _strip_unsourced_numbers(
        "", ["Render generatif motong siklus lewat modeling 3D."], "", TOPIC, ""
    )
    assert fabricated == set()
    assert parts == ["Render generatif motong siklus lewat modeling 3D."]


def test_bonus_reply_is_also_checked():
    # The withheld fragment goes to the reply section, where it is still read
    # as fact, so it is held to the same rule.
    _, _, bonus, fabricated = _strip_unsourced_numbers(
        "", [""], "Satu operator AI kini beban kerja 3 orang.", TOPIC, ""
    )
    assert fabricated == {"3"}
    assert bonus == "Satu operator AI kini beban kerja orang."


def test_cleaned_text_has_no_double_spaces():
    _, parts, _, _ = _strip_unsourced_numbers(
        "", ["Pangkas 70% dari total 100% anggaran."], "", TOPIC, ""
    )
    assert "  " not in parts[0]


def test_empty_inputs_do_not_crash():
    assert _strip_unsourced_numbers("", [], "", TOPIC, "") == ("", [], "", set())
