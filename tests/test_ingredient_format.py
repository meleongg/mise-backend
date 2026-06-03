"""Tests for ingredient display formatting."""

from app.utils.ingredient_format import (
    format_ingredient_display,
    normalize_ingredient_pair,
)


def test_to_taste_goes_after_name():
    assert format_ingredient_display("Salt", "to taste") == "Salt, to taste"
    assert format_ingredient_display("Black pepper", "to taste") == (
        "Black pepper, to taste"
    )


def test_standard_measure_before_name():
    assert format_ingredient_display("broccoli florets", "2 cups") == (
        "2 cups broccoli florets"
    )


def test_stray_comma_after_quantity():
    assert format_ingredient_display("sliced bell pepper", "1,") == (
        "1 sliced bell pepper"
    )


def test_comma_between_quantity_and_prep_in_measure():
    assert format_ingredient_display("bell pepper", "1, sliced") == (
        "1 sliced bell pepper"
    )


def test_normalize_to_taste_pair():
    name, measure = normalize_ingredient_pair("Salt", "to taste,")
    assert name == "Salt"
    assert measure == "to taste"
