"""Tests for the theme engine (spec validation, CSS generation, raw CSS guard)."""

import json

from app.theme import (
    DEFAULT_SPEC, get_preset, normalize_theme_patch,
    theme_spec_to_css, validate_raw_css, validate_theme_spec,
)


def test_default_spec_validates_and_generates_triplets():
    spec, errors = validate_theme_spec(DEFAULT_SPEC)
    assert spec is not None and not errors
    css = theme_spec_to_css(spec)
    assert "--theme-bg: 16 19 31;" in css  # Extrovert · Ink (dark) default
    assert "--theme-accent: 255 92 138;" in css
    assert "--theme-sidebar-width: 18rem;" in css
    assert "--theme-bubble-max-width: 75%;" in css
    assert ":root {" in css


def test_invalid_color_rejected():
    spec, errors = validate_theme_spec({"tokens": {"colors": {"accent": "notacolor"}}})
    assert spec is None
    assert any("tokens.colors.accent" in e for e in errors)


def test_alpha_color_rejected_in_tokens_but_ok_in_components():
    spec, errors = validate_theme_spec({"tokens": {"colors": {"accent": "#ff5c8aaa"}}})
    assert spec is None

    spec, errors = validate_theme_spec({
        "tokens": {"colors": {"accent": "#ff5c8a"}},
        "components": {"sidebar": {"background": "rgb(18 23 39 / 0.6)"}},
    })
    assert spec is not None and not errors
    css = theme_spec_to_css(spec)
    assert ".llm-sidebar {" in css
    assert "background: rgb(18 23 39 / 0.6);" in css
    assert "border-style" not in css  # no border props -> no auto border-style

    spec2, errors = validate_theme_spec({
        "components": {"button": {"border-width": "2px", "border-color": "#ff4d00"}},
    })
    assert spec2 is not None and not errors
    css2 = theme_spec_to_css(spec2)
    assert "border-style: solid;" in css2  # auto-added when border-width set


def test_invalid_length_rejected():
    spec, errors = validate_theme_spec({"tokens": {"layout": {"sidebar": "18miles"}}})
    assert spec is None
    assert any("tokens.layout.sidebar" in e for e in errors)


def test_unknown_tokens_and_components_rejected():
    spec, errors = validate_theme_spec({"tokens": {"colors": {"nope": "#fff"}}})
    assert spec is None
    spec, errors = validate_theme_spec({"components": {"evil": {"background": "#fff"}}})
    assert spec is None
    spec, errors = validate_theme_spec({
        "components": {"sidebar": {"position": "fixed"}},
    })
    assert spec is None
    assert any("components.sidebar.position" in e for e in errors)


def test_backdrop_filter_validation():
    ok_spec, errors = validate_theme_spec({
        "components": {"modal": {"backdrop-filter": "blur(24px) saturate(1.4)"}},
    })
    assert ok_spec is not None and not errors
    bad_spec, errors = validate_theme_spec({
        "components": {"modal": {"backdrop-filter": "url(https://evil.example)"}},
    })
    assert bad_spec is None


def test_normalize_theme_patch_merge_and_preset():
    base, _ = validate_theme_spec(DEFAULT_SPEC)
    merged, errors = normalize_theme_patch(base, {
        "tokens": {"colors": {"accent": "#ff5c8a"}, "layout": {"sidebar": "320px"}},
    })
    assert merged is not None and not errors
    assert merged["tokens"]["colors"]["accent"] == "255 92 138"
    assert merged["tokens"]["layout"]["sidebar"] == "320px"
    # preset switch replaces base
    preset, errors = normalize_theme_patch(base, {"preset": "extrovert-light"})
    assert preset is not None and not errors
    assert preset["preset"] == "extrovert-light"
    css = theme_spec_to_css(preset)
    assert "--theme-bg: 246 245 251;" in css  # light theme actually applied
    assert "--theme-accent: 232 53 122;" in css
    # null deletes a token
    merged2, errors = normalize_theme_patch(base, {"tokens": {"layout": {"sidebar": None}}})
    assert merged2 is not None and not errors
    assert "sidebar" in merged2["tokens"]["layout"]  # falls back to default (full spec)


def test_unknown_preset_rejected():
    base, _ = validate_theme_spec(DEFAULT_SPEC)
    _, errors = normalize_theme_patch(base, {"preset": "neon"})
    assert errors


def test_presets_all_valid():
    for name in ("extrovert", "extrovert-light"):
        spec, errors = validate_theme_spec(get_preset(name))
        assert spec is not None and not errors, name
        css = theme_spec_to_css(spec)
        assert ":root {" in css


def test_raw_css_guard():
    assert validate_raw_css("a { color: red; }") == (True, "")
    assert validate_raw_css("a { background: url(https://evil.example/x.png); }")[0] is False
    assert validate_raw_css("@import url('https://evil.example/theme.css');")[0] is False
    assert validate_raw_css("@media (max-width: 600px) { a { color: blue } }") == (True, "")
    assert validate_raw_css("@font-face { font-family: X; }")[0] is False
    assert validate_raw_css("a { color: red")[0] is False  # unbalanced braces
    assert validate_raw_css("a { behavior: url(#default#VML); }")[0] is False
    assert validate_raw_css("x { expression(alert(1)); }")[0] is False
    assert validate_raw_css("a {" + "x" * 300_000 + "}")[0] is False  # size cap
