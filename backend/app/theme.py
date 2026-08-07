"""Theme engine for LLMDash.

The theme is a structured, validated spec (JSON) that is converted into CSS
variables (+ optional component rules). The model never writes raw CSS for
the theme — it edits validated token values. Raw-CSS tools still exist for
power users but are run through `validate_raw_css()` which blocks
exfiltration/JS constructs (url(), @import, ...).

Security model:
  - themes are per-user, applied client-side in the user's own session only
  - token values are validated against strict regexes (no injection)
  - component rules use an allowlist of components AND properties, with
    per-property validators
  - generated CSS never contains url() / @import / expression() / ...
  - raw CSS saves are validated and size-capped
"""

import json
import re

MAX_THEME_SPEC_CHARS = 200_000
MAX_RAW_CSS_CHARS = 256_000

# --------------------------------------------------------------------------
# Value validators
# --------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,6}$")
_HEX_ALPHA_RE = re.compile(r"^#[0-9a-fA-F]{8}$")
# Already-normalized triplet ("3 7 18") — emitted by theme_spec_to_css, must
# round-trip through validation.
_TRIPLET_RE = re.compile(r"^\d{1,3}\s+\d{1,3}\s+\d{1,3}$")
_FUNC_COLOR_RE = re.compile(
    r"^(rgb|rgba|hsl|hsla)\(\s*[\d.]+(\s*,\s*[\d.%]+){2,3}\s*\)$"
)
_SPACED_COLOR_RE = re.compile(
    r"^(rgb|rgba|hsl|hsla)\(\s*[\d.%]+\s+[\d.%]+\s+[\d.%]+\s*(/\s*[\d.]+%?)?\s*\)$"
)
_LENGTH_UNIT_RE = re.compile(r"^-?\d+(\.\d+)?(px|rem|em|%|vh|vw|vmin|vmax|ch|ex|pt|fr)$")
_UNITLESS_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DURATION_RE = re.compile(r"^-?\d+(\.\d+)?(ms|s)$")
_EASE_RE = re.compile(
    r"^(ease|linear|ease-in|ease-out|ease-in-out|"
    r"cubic-bezier\(-?[\d.]+(,\s*-?[\d.]+){3}\)|steps\(\d+\))$"
)
_FONT_RE = re.compile(r"^[A-Za-z0-9 ,'\"\-._]+$")
_SHADOW_RE = re.compile(r"^[A-Za-z0-9#(),.\s%\-/]+$")
_BACKDROP_RE = re.compile(
    r"^(none|blur\(\d+(\.\d+)?px\)|saturate\(\d+(\.\d+)?\)|brightness\(\d+(\.\d+)?\)|"
    r"contrast\(\d+(\.\d+)?\))(\s+(blur|saturate|brightness|contrast)"
    r"\(\d+(\.\d+)?(px)?\))*$"
)
_KEYWORDS = {"none", "auto", "solid", "hidden", "dotted", "dashed", "double",
             "groove", "ridge", "inset", "outset", "transparent", "currentColor"}

_NAMED_COLORS = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
    "pink": "#ffc0cb", "brown": "#a52a2a", "navy": "#000080", "teal": "#008080",
    "silver": "#c0c0c0", "lime": "#00ff00", "maroon": "#800000", "olive": "#808000",
    "gold": "#ffd700", "salmon": "#fa8072", "tomato": "#ff6347", "coral": "#ff7f50",
    "indigo": "#4b0082", "violet": "#ee82ee", "turquoise": "#40e0d0",
    "skyblue": "#87ceeb", "beige": "#f5f5dc", "ivory": "#fffff0",
}


def _hex_to_triplet(hex_str: str) -> str:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"{r} {g} {b}"


def validate_token_value(kind: str, value) -> tuple[bool, str, str]:
    """Validate a token value. Returns (ok, normalized_value, error)."""
    if isinstance(value, bool) or value is None:
        return False, "", "must be a string or number"
    s = str(value).strip()
    if not s:
        return False, "", "must not be empty"

    if kind == "color":
        if _HEX_RE.match(s):
            return True, _hex_to_triplet(s), ""
        if _HEX_ALPHA_RE.match(s):
            return False, "", "alpha colors are only allowed in components, not tokens"
        if _TRIPLET_RE.match(s):
            return True, s, ""
        if s.lower() in _NAMED_COLORS:
            return True, _hex_to_triplet(_NAMED_COLORS[s.lower()]), ""
        return False, "", f"'{s}' is not a valid color (use #hex or a CSS color name)"
    if kind == "length":
        if _LENGTH_UNIT_RE.match(s):
            return True, s, ""
        if _UNITLESS_RE.match(s) and float(s) == 0:
            return True, "0", ""
        return False, "", f"'{s}' is not a valid length (e.g. 12px, 1.5rem, 80%, 0)"
    if kind == "unitless":
        if _UNITLESS_RE.match(s) and 0 <= float(s) <= 900:
            return True, str(int(float(s))) if float(s).is_integer() else s, ""
        return False, "", f"'{s}' is not a valid number"
    if kind == "duration":
        if _DURATION_RE.match(s):
            return True, s, ""
        return False, "", f"'{s}' is not a valid duration (e.g. 200ms, 1.5s)"
    if kind == "ease":
        if _EASE_RE.match(s):
            return True, s, ""
        return False, "", f"'{s}' is not a valid easing function"
    if kind == "font":
        if len(s) > 200:
            return False, "", "font value too long"
        if ";" in s or "url(" in s.lower():
            return False, "", "fonts may not contain ';' or url()"
        if _FONT_RE.match(s):
            return True, s, ""
        return False, "", f"'{s}' is not a valid font-family value"
    if kind == "shadow":
        if len(s) > 300:
            return False, "", "shadow value too long"
        if ";" in s or "url(" in s.lower():
            return False, "", "shadows may not contain ';' or url()"
        if _SHADOW_RE.match(s) or s == "none":
            return True, s, ""
        return False, "", f"'{s}' is not a valid box-shadow"
    return False, "", f"unknown token kind '{kind}'"


def validate_component_value(prop: str, value) -> tuple[bool, str, str]:
    """Validate a component rule property value. Returns (ok, normalized, error)."""
    if isinstance(value, bool) or value is None:
        return False, "", "must be a string or number"
    s = str(value).strip()
    if not s:
        return False, "", "must not be empty"

    if prop in ("background", "color", "border-color"):
        if _HEX_RE.match(s) or _HEX_ALPHA_RE.match(s):
            return True, s, ""
        if _FUNC_COLOR_RE.match(s) or _SPACED_COLOR_RE.match(s):
            return True, s, ""
        if s.lower() in _NAMED_COLORS or s.lower() in ("transparent", "currentColor"):
            return True, s, ""
        return False, "", f"'{s}' is not a valid color"
    if prop in ("border-width", "border-radius", "padding", "margin", "max-width",
                "width", "height", "font-size"):
        if s in ("auto", "none"):
            return True, s, ""
        if _LENGTH_UNIT_RE.match(s):
            return True, s, ""
        if _UNITLESS_RE.match(s) and float(s) == 0:
            return True, "0", ""
        return False, "", f"'{s}' is not a valid length"
    if prop == "border-style":
        if s in ("none", "hidden", "dotted", "dashed", "solid", "double",
                 "groove", "ridge", "inset", "outset"):
            return True, s, ""
        return False, "", f"'{s}' is not a valid border-style"
    if prop == "opacity":
        if _UNITLESS_RE.match(s) and 0 <= float(s) <= 1:
            return True, s, ""
        return False, "", "opacity must be between 0 and 1"
    if prop == "font-weight":
        if s in ("normal", "bold", "bolder", "lighter") or (
            _UNITLESS_RE.match(s) and 100 <= float(s) <= 900
        ):
            return True, s, ""
        return False, "", "font-weight must be 100-900 or a keyword"
    if prop == "font-family":
        if len(s) > 200 or ";" in s or "url(" in s.lower():
            return False, "", "font-family may not contain ';' or url()"
        if _FONT_RE.match(s):
            return True, s, ""
        return False, "", f"'{s}' is not a valid font-family"
    if prop == "box-shadow":
        if len(s) > 300 or ";" in s or "url(" in s.lower():
            return False, "", "box-shadow may not contain ';' or url()"
        if _SHADOW_RE.match(s) or s == "none":
            return True, s, ""
        return False, "", f"'{s}' is not a valid box-shadow"
    if prop == "backdrop-filter":
        if _BACKDROP_RE.match(s):
            return True, s, ""
        return False, "", "backdrop-filter must be blur()/saturate()/brightness()/contrast() combos"
    return False, "", f"property '{prop}' is not allowed"


# --------------------------------------------------------------------------
# Theme spec schema
# --------------------------------------------------------------------------

# token section -> { spec key -> (css var suffix, kind) }
TOKEN_SCHEMA: dict[str, dict[str, tuple[str, str]]] = {
    "colors": {
        "bg": ("--theme-bg", "color"),
        "bg_secondary": ("--theme-bg-secondary", "color"),
        "bg_elevated": ("--theme-bg-elevated", "color"),
        "bg_hover": ("--theme-bg-hover", "color"),
        "bg_active": ("--theme-bg-active", "color"),
        "overlay": ("--theme-overlay", "color"),
        "msg_user": ("--theme-msg-user", "color"),
        "msg_ai": ("--theme-msg-ai", "color"),
        "code_bg": ("--theme-code-bg", "color"),
        "text": ("--theme-text", "color"),
        "text_secondary": ("--theme-text-secondary", "color"),
        "muted": ("--theme-text-muted", "color"),
        "subtle": ("--theme-text-subtle", "color"),
        "accent_text": ("--theme-text-accent", "color"),
        "accent_dim": ("--theme-text-accent-dim", "color"),
        "danger_text": ("--theme-text-danger", "color"),
        "icon_muted": ("--theme-icon-muted", "color"),
        "icon_user": ("--theme-icon-user", "color"),
        "icon_ai": ("--theme-icon-ai", "color"),
        "accent": ("--theme-accent", "color"),
        "accent_hover": ("--theme-accent-hover", "color"),
        "danger": ("--theme-danger", "color"),
        "danger_hover": ("--theme-danger-hover", "color"),
        "border": ("--theme-border", "color"),
        "border_light": ("--theme-border-light", "color"),
        "purple": ("--theme-purple", "color"),
        "amber": ("--theme-amber", "color"),
        "switch_off": ("--theme-switch-off", "color"),
        "blue": ("--theme-blue", "color"),
        "blue_text": ("--theme-blue-text", "color"),
        "spinner": ("--theme-spinner", "color"),
        "focus_ring": ("--theme-focus-ring", "color"),
        "preview_bg": ("--theme-preview-bg", "color"),
    },
    "fonts": {
        "sans": ("--theme-font-sans", "font"),
        "mono": ("--theme-font-mono", "font"),
    },
    "radius": {
        "default": ("--theme-radius", "length"),
        "sm": ("--theme-radius-sm", "length"),
        "md": ("--theme-radius-md", "length"),
        "lg": ("--theme-radius-lg", "length"),
        "xl": ("--theme-radius-xl", "length"),
        "2xl": ("--theme-radius-2xl", "length"),
        "full": ("--theme-radius-full", "length"),
    },
    "shadows": {
        "xl": ("--theme-shadow-xl", "shadow"),
        "2xl": ("--theme-shadow-2xl", "shadow"),
    },
    "typography": {
        "xs": ("--theme-text-xs", "length"),
        "sm": ("--theme-text-sm", "length"),
        "base": ("--theme-text-base", "length"),
        "lg": ("--theme-text-lg", "length"),
        "xl": ("--theme-text-xl", "length"),
        "2xl": ("--theme-text-2xl", "length"),
    },
    "weights": {
        "medium": ("--theme-font-medium", "unitless"),
        "semibold": ("--theme-font-semibold", "unitless"),
        "bold": ("--theme-font-bold", "unitless"),
    },
    "motion": {
        "duration": ("--theme-duration", "duration"),
        "ease": ("--theme-ease", "ease"),
        "spin": ("--theme-spin-duration", "duration"),
        "pulse": ("--theme-pulse-duration", "duration"),
    },
    "layout": {
        "sidebar": ("--theme-sidebar-width", "length"),
        "chat_max": ("--theme-chat-max-width", "length"),
        "panel_max": ("--theme-panel-max-width", "length"),
        "dropdown": ("--theme-dropdown-width", "length"),
        "bubble_max": ("--theme-bubble-max-width", "length"),
        "side_panel": ("--theme-side-panel-width", "length"),
        "avatar": ("--theme-avatar-size", "length"),
        "message_gap": ("--theme-message-gap", "length"),
        "preview_height": ("--theme-preview-height", "length"),
        "auth_max": ("--theme-auth-max-width", "length"),
        "z_modal": ("--theme-z-modal", "unitless"),
    },
}

COMPONENT_SCHEMA: dict[str, dict[str, str]] = {
    "sidebar": {"background": "background", "width": "width"},
    "bubble": {"background": "background"},
    "bubble_user": {"background": "background"},
    "bubble_assistant": {"background": "background"},
    "input": {"background": "background"},
    "button": {"background": "background"},
    "modal": {"background": "background"},
    "avatar": {"background": "background"},
    "preview_panel": {"background": "background"},
    "code_block": {"background": "background"},
    "tool_pill": {"background": "background"},
}

# Every property that may appear on any component (validators shared above).
ALLOWED_COMPONENT_PROPS = {
    "background", "color", "border-color", "border-radius", "border-width",
    "border-style", "box-shadow", "padding", "margin", "max-width", "width",
    "height", "opacity", "backdrop-filter", "font-family", "font-size",
    "font-weight",
}

ALLOWED_PRESETS = ("extrovert", "extrovert-light")


# --------------------------------------------------------------------------
# Defaults + presets
# --------------------------------------------------------------------------

DEFAULT_SPEC: dict = {
    "preset": "extrovert",
    "tokens": {
        "colors": {
            "bg": "#10131f", "bg_secondary": "#181b2e", "bg_elevated": "#212540",
            "bg_hover": "#2a2f4d", "bg_active": "#2a2f4d", "overlay": "#000000",
            "msg_user": "#38243b", "msg_ai": "#181b2e", "code_bg": "#1b1e30",
            "text": "#f2f0fb", "text_secondary": "#a8aacc", "muted": "#7679a0",
            "subtle": "#8f92b8", "accent_text": "#ff5c8a", "accent_dim": "#7aecef",
            "danger_text": "#ff5d6c", "icon_muted": "#3a3f5e", "icon_user": "#ff5c8a",
            "icon_ai": "#5cdfe0", "accent": "#ff5c8a", "accent_hover": "#ff7da3",
            "danger": "#ff5d6c", "danger_hover": "#ff7a85", "border": "#2a2e48",
            "border_light": "#3a3f5e", "purple": "#a78bfa", "amber": "#ffce4d",
            "switch_off": "#484d6a", "blue": "#5cdfe0", "blue_text": "#7aecef",
            "spinner": "#ff5c8a", "focus_ring": "#ff7da3", "preview_bg": "#ffffff",
        },
        "fonts": {
            "sans": "'Hanken Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
            "mono": "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
        },
        "radius": {
            "default": "0.75rem", "sm": "0.5rem", "md": "0.75rem", "lg": "1rem",
            "xl": "1.375rem", "2xl": "1.5rem", "full": "9999px",
        },
        "shadows": {
            "xl": "0 6px 22px rgb(0 0 0 / 0.28)",
            "2xl": "0 18px 48px rgb(0 0 0 / 0.38)",
        },
        "typography": {
            "xs": "0.75rem", "sm": "0.875rem", "base": "1rem", "lg": "1.125rem",
            "xl": "1.25rem", "2xl": "1.5rem",
        },
        "weights": {"medium": 500, "semibold": 600, "bold": 700},
        "motion": {
            "duration": "200ms", "ease": "cubic-bezier(0.4, 0, 0.2, 1)",
            "spin": "1s", "pulse": "2s",
        },
        "layout": {
            "sidebar": "18rem", "chat_max": "56rem", "panel_max": "48rem",
            "dropdown": "16rem", "bubble_max": "75%", "side_panel": "420px",
            "avatar": "2rem", "message_gap": "1rem", "preview_height": "24rem",
            "auth_max": "24rem", "z_modal": 50,
        },
    },
    "components": {},
}

PRESETS: dict[str, dict] = {
    "extrovert": DEFAULT_SPEC,
    "extrovert-light": {
        "preset": "extrovert-light",
        "tokens": {
            "colors": {
                "bg": "#f6f5fb", "bg_secondary": "#ffffff", "bg_elevated": "#f0eef7",
                "bg_hover": "#e6e3f2", "bg_active": "#e6e3f2", "overlay": "#000000",
                "msg_user": "#fdebef", "msg_ai": "#ffffff", "code_bg": "#f3f1f9",
                "text": "#1a1d2e", "text_secondary": "#5a5d7a", "muted": "#8a8da8",
                "subtle": "#6e718d", "accent_text": "#e8357a", "accent_dim": "#079aa6",
                "danger_text": "#d63342", "icon_muted": "#c4c0d6", "icon_user": "#e8357a",
                "icon_ai": "#06b6c4", "accent": "#e8357a", "accent_hover": "#c91f63",
                "danger": "#d63342", "danger_hover": "#b42836", "border": "#dcd9e8",
                "border_light": "#c4c0d6", "purple": "#7c60c8", "amber": "#d4900f",
                "switch_off": "#c4c0d6", "blue": "#06b6c4", "blue_text": "#079aa6",
                "spinner": "#e8357a", "focus_ring": "#c91f63", "preview_bg": "#ffffff",
            },
        },
        "components": {},
    },
}

def _deep_merge(base: dict, patch: dict) -> dict:
    """JSON-merge-patch style merge: dicts merge recursively, None deletes."""
    for key, value in patch.items():
        if value is None:
            base.pop(key, None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_preset(name: str) -> dict | None:
    if name not in ALLOWED_PRESETS:
        return None
    spec = json.loads(json.dumps(PRESETS[name]))  # deep copy
    return spec


def validate_theme_spec(spec: dict) -> tuple[dict | None, list[str]]:
    """Validate a full (or partial) theme spec.

    Returns (normalized_spec, errors). The normalized spec always contains a
    complete set of tokens (defaults merged) and validated components.
    """
    errors: list[str] = []
    if not isinstance(spec, dict):
        return None, ["theme spec must be a JSON object"]

    tokens = spec.get("tokens") or {}
    if not isinstance(tokens, dict):
        errors.append("'tokens' must be an object")
        tokens = {}

    normalized_tokens: dict[str, dict] = {}
    for section, keys in TOKEN_SCHEMA.items():
        given = tokens.get(section) or {}
        if not isinstance(given, dict):
            errors.append(f"tokens.{section} must be an object")
            continue
        for key, (var, kind) in keys.items():
            default = DEFAULT_SPEC["tokens"].get(section, {}).get(key)
            if key in given:
                ok, norm, err = validate_token_value(kind, given[key])
                if ok:
                    normalized_tokens.setdefault(section, {})[key] = norm
                else:
                    errors.append(f"tokens.{section}.{key}: {err}")
            elif default is not None:
                _, norm, _ = validate_token_value(kind, default)
                normalized_tokens.setdefault(section, {})[key] = norm
        # reject unknown keys in a section
        known = set(keys.keys())
        for extra in set((given or {}).keys()) - known:
            errors.append(f"tokens.{section}.{extra}: unknown token")

    components = spec.get("components") or {}
    if not isinstance(components, dict):
        errors.append("'components' must be an object")
        components = {}
    normalized_components: dict[str, dict] = {}
    for comp_name, props in components.items():
        if comp_name not in COMPONENT_SCHEMA:
            errors.append(f"components.{comp_name}: unknown component")
            continue
        if not isinstance(props, dict):
            errors.append(f"components.{comp_name} must be an object")
            continue
        for prop, value in props.items():
            if prop not in ALLOWED_COMPONENT_PROPS:
                errors.append(f"components.{comp_name}.{prop}: property not allowed")
                continue
            ok, norm, err = validate_component_value(prop, value)
            if ok:
                normalized_components.setdefault(comp_name, {})[prop] = norm
            else:
                errors.append(f"components.{comp_name}.{prop}: {err}")

    if errors:
        return None, errors

    normalized = {
        "preset": spec.get("preset", "custom") if isinstance(spec.get("preset", "custom"), str) else "custom",
        "tokens": normalized_tokens,
        "components": normalized_components,
    }
    return normalized, []


def theme_spec_to_css(spec: dict) -> str:
    """Generate the CSS string for a validated theme spec."""
    tokens = spec.get("tokens") or {}
    lines = [":root {"]
    for section, keys in TOKEN_SCHEMA.items():
        for key, (var, kind) in keys.items():
            section_tokens = tokens.get(section) or {}
            if key in section_tokens:
                lines.append(f"  {var}: {section_tokens[key]};")
    lines.append("}")

    components = spec.get("components") or {}
    for comp_name, props in components.items():
        cls = f".llm-{comp_name}"
        has_border = any(p in props for p in ("border-width", "border-color"))
        if has_border and "border-style" not in props:
            props = dict(props)
            props["border-style"] = "solid"
        rule = f"{cls} {{\n"
        for prop, value in props.items():
            rule += f"  {prop}: {value};\n"
        rule += "}"
        lines.append(rule)

    return "\n".join(lines)


def normalize_theme_patch(base_spec: dict, patch: dict) -> tuple[dict | None, list[str]]:
    """Apply a JSON merge patch (tokens/components/preset) on top of a base spec.

    If 'preset' is present in the patch, the base becomes that preset's spec
    first. Returns (new_spec, errors) where new_spec is fully validated.
    """
    spec = json.loads(json.dumps(base_spec))

    preset = patch.get("preset")
    if isinstance(preset, str):
        preset_spec = get_preset(preset)
        if preset_spec is None:
            return None, [f"unknown preset '{preset}'. Available: {', '.join(ALLOWED_PRESETS)}"]
        spec = preset_spec

    merged = _deep_merge(spec, {k: v for k, v in patch.items() if k in ("tokens", "components")})
    if preset:
        merged["preset"] = preset if merged.get("preset") == preset else merged.get("preset", "custom")

    return validate_theme_spec(merged)


# --------------------------------------------------------------------------
# Raw CSS validation (legacy patch_user_css / append_user_css / set_user_css)
# --------------------------------------------------------------------------

_RAW_CSS_BLOCKED = [
    re.compile(r"url\s*\(", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),
    re.compile(r"@document", re.IGNORECASE),
    re.compile(r"@-moz-document", re.IGNORECASE),
    re.compile(r"@charset", re.IGNORECASE),
    re.compile(r"expression\s*\(", re.IGNORECASE),
    re.compile(r"-moz-binding", re.IGNORECASE),
    re.compile(r"behavior\s*:", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"@font-face", re.IGNORECASE),
]

_ALLOWED_AT_RULES = {"@media", "@supports", "@keyframes", "@-webkit-keyframes"}


def validate_raw_css(css: str) -> tuple[bool, str]:
    """Validate a raw user CSS string. Returns (ok, error_message)."""
    if css is None:
        return True, ""
    css = str(css)
    if len(css) > MAX_RAW_CSS_CHARS:
        return False, f"stylesheet too large (max {MAX_RAW_CSS_CHARS} chars)"

    lower = css.lower()
    for pattern in _RAW_CSS_BLOCKED:
        if pattern.search(lower):
            return False, f"blocked construct: {pattern.pattern}"

    for line in css.splitlines():
        stripped = line.strip()
        if stripped.startswith("@"):
            at_rule = stripped.split(None, 1)[0].split("{", 1)[0].rstrip()
            if at_rule not in _ALLOWED_AT_RULES:
                return False, f"at-rule '{at_rule}' is not allowed"

    if css.count("{") != css.count("}"):
        return False, "unbalanced braces"

    return True, ""
