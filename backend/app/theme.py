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

ALLOWED_PRESETS = ("default", "compact", "glassmorphism", "brutalism")


# --------------------------------------------------------------------------
# Defaults + presets
# --------------------------------------------------------------------------

DEFAULT_SPEC: dict = {
    "preset": "default",
    "tokens": {
        "colors": {
            "bg": "#030712", "bg_secondary": "#111827", "bg_elevated": "#1f2937",
            "bg_hover": "#374151", "bg_active": "#374151", "overlay": "#000000",
            "msg_user": "#047857", "msg_ai": "#1f2937", "code_bg": "#1a1b26",
            "text": "#f3f4f6", "text_secondary": "#d1d5db", "muted": "#6b7280",
            "subtle": "#9ca3af", "accent_text": "#34d399", "accent_dim": "#6ee7b7",
            "danger_text": "#f87171", "icon_muted": "#374151", "icon_user": "#2563eb",
            "icon_ai": "#059669", "accent": "#059669", "accent_hover": "#10b981",
            "danger": "#dc2626", "danger_hover": "#ef4444", "border": "#1f2937",
            "border_light": "#374151", "purple": "#c084fc", "amber": "#fbbf24",
            "switch_off": "#4b5563", "blue": "#2563eb", "blue_text": "#93c5fd",
            "spinner": "#34d399", "focus_ring": "#10b981", "preview_bg": "#ffffff",
        },
        "fonts": {
            "sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            "mono": "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
        },
        "radius": {
            "default": "0.25rem", "sm": "0.125rem", "md": "0.375rem", "lg": "0.5rem",
            "xl": "0.75rem", "2xl": "1rem", "full": "9999px",
        },
        "shadows": {
            "xl": "0 20px 25px -5px rgb(0 0 0 / 0.1)",
            "2xl": "0 25px 50px -12px rgb(0 0 0 / 0.25)",
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
    "default": DEFAULT_SPEC,
    "compact": {
        "preset": "compact",
        "tokens": {
            "colors": {
                "bg": "#0b0e14", "bg_secondary": "#151922", "bg_elevated": "#1d2330",
                "bg_hover": "#2a3242", "bg_active": "#2a3242", "overlay": "#000000",
                "msg_user": "#0d6e5a", "msg_ai": "#1d2330", "code_bg": "#12151d",
                "text": "#eef1f6", "text_secondary": "#c8cedb", "muted": "#6c7484",
                "subtle": "#97a0b3", "accent_text": "#3ddc9d", "accent_dim": "#6fe8bc",
                "danger_text": "#f87171", "icon_muted": "#2a3242", "icon_user": "#3b82f6",
                "icon_ai": "#10b981", "accent": "#10b981", "accent_hover": "#34d399",
                "danger": "#dc2626", "danger_hover": "#ef4444", "border": "#1d2330",
                "border_light": "#2a3242", "purple": "#a78bfa", "amber": "#fbbf24",
                "switch_off": "#3f4859", "blue": "#3b82f6", "blue_text": "#93c5fd",
                "spinner": "#3ddc9d", "focus_ring": "#34d399", "preview_bg": "#0b0e14",
            },
            "fonts": {
                "sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                "mono": "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
            },
            "radius": {
                "default": "0.2rem", "sm": "0.125rem", "md": "0.25rem", "lg": "0.4rem",
                "xl": "0.6rem", "2xl": "0.75rem", "full": "9999px",
            },
            "shadows": {
                "xl": "0 12px 18px -6px rgb(0 0 0 / 0.2)",
                "2xl": "0 16px 28px -8px rgb(0 0 0 / 0.3)",
            },
            "typography": {
                "xs": "0.7rem", "sm": "0.8125rem", "base": "0.9375rem", "lg": "1.05rem",
                "xl": "1.15rem", "2xl": "1.375rem",
            },
            "weights": {"medium": 500, "semibold": 600, "bold": 700},
            "motion": {
                "duration": "150ms", "ease": "cubic-bezier(0.4, 0, 0.2, 1)",
                "spin": "1s", "pulse": "2s",
            },
            "layout": {
                "sidebar": "14rem", "chat_max": "48rem", "panel_max": "42rem",
                "dropdown": "14rem", "bubble_max": "65%", "side_panel": "360px",
                "avatar": "1.75rem", "message_gap": "0.5rem", "preview_height": "20rem",
                "auth_max": "22rem", "z_modal": 50,
            },
        },
        "components": {},
    },
    "glassmorphism": {
        "preset": "glassmorphism",
        "tokens": {
            "colors": {
                "bg": "#0a0e1a", "bg_secondary": "#121727", "bg_elevated": "#1b2136",
                "bg_hover": "#2a3150", "bg_active": "#2a3150", "overlay": "#000000",
                "msg_user": "#ff5c8a", "msg_ai": "#1b2136", "code_bg": "#0d1120",
                "text": "#eef0f8", "text_secondary": "#c6cbe0", "muted": "#6b7290",
                "subtle": "#98a0c0", "accent_text": "#ff8fae", "accent_dim": "#ffc2d3",
                "danger_text": "#ff8fa3", "icon_muted": "#2a3150", "icon_user": "#5cdfe0",
                "icon_ai": "#ff5c8a", "accent": "#ff5c8a", "accent_hover": "#ff7aa0",
                "danger": "#ff5c7a", "danger_hover": "#ff7a93", "border": "#262d4a",
                "border_light": "#3a4370", "purple": "#c084fc", "amber": "#ffce4d",
                "switch_off": "#3a4370", "blue": "#5cdfe0", "blue_text": "#a8ecee",
                "spinner": "#ff8fae", "focus_ring": "#ff5c8a", "preview_bg": "#0a0e1a",
            },
            "fonts": {
                "sans": "Hanken Grotesk, -apple-system, 'Segoe UI', Roboto, sans-serif",
                "mono": "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
            },
            "radius": {
                "default": "1rem", "sm": "0.5rem", "md": "0.875rem", "lg": "1.25rem",
                "xl": "1.5rem", "2xl": "1.75rem", "full": "9999px",
            },
            "shadows": {
                "xl": "0 20px 40px -12px rgb(0 0 0 / 0.5)",
                "2xl": "0 30px 60px -12px rgb(0 0 0 / 0.55)",
            },
            "typography": {
                "xs": "0.75rem", "sm": "0.875rem", "base": "1rem", "lg": "1.125rem",
                "xl": "1.25rem", "2xl": "1.5rem",
            },
            "weights": {"medium": 500, "semibold": 600, "bold": 700},
            "motion": {
                "duration": "240ms", "ease": "cubic-bezier(0.2, 0.8, 0.2, 1)",
                "spin": "1s", "pulse": "2s",
            },
            "layout": {
                "sidebar": "18rem", "chat_max": "56rem", "panel_max": "48rem",
                "dropdown": "16rem", "bubble_max": "75%", "side_panel": "420px",
                "avatar": "2.25rem", "message_gap": "1.25rem", "preview_height": "26rem",
                "auth_max": "24rem", "z_modal": 50,
            },
        },
        "components": {
            "sidebar": {
                "background": "rgb(18 23 39 / 0.65)", "backdrop-filter": "blur(18px) saturate(1.4)",
                "border-color": "rgb(58 67 112 / 0.5)", "border-width": "1px",
            },
            "bubble": {
                "background": "rgb(27 33 54 / 0.55)", "backdrop-filter": "blur(12px) saturate(1.2)",
                "border-color": "rgb(58 67 112 / 0.4)", "border-width": "1px",
            },
            "modal": {
                "background": "rgb(18 23 39 / 0.85)", "backdrop-filter": "blur(24px) saturate(1.4)",
                "border-color": "rgb(58 67 112 / 0.5)", "border-width": "1px",
            },
            "input": {
                "background": "rgb(27 33 54 / 0.5)", "backdrop-filter": "blur(10px)",
                "border-color": "rgb(58 67 112 / 0.5)", "border-width": "1px",
            },
            "button": {
                "background": "rgb(255 92 138 / 0.9)", "backdrop-filter": "blur(8px)",
                "border-color": "rgb(255 92 138 / 0.3)", "border-width": "1px",
            },
            "preview_panel": {
                "background": "rgb(18 23 39 / 0.75)", "backdrop-filter": "blur(20px)",
                "border-color": "rgb(58 67 112 / 0.5)", "border-width": "1px",
            },
            "avatar": {"background": "rgb(255 92 138 / 0.85)"},
        },
    },
    "brutalism": {
        "preset": "brutalism",
        "tokens": {
            "colors": {
                "bg": "#101010", "bg_secondary": "#171717", "bg_elevated": "#1f1f1f",
                "bg_hover": "#2c2c2c", "bg_active": "#2c2c2c", "overlay": "#000000",
                "msg_user": "#ff4d00", "msg_ai": "#1f1f1f", "code_bg": "#0a0a0a",
                "text": "#fafafa", "text_secondary": "#d4d4d4", "muted": "#808080",
                "subtle": "#a3a3a3", "accent_text": "#ff4d00", "accent_dim": "#ff7a3d",
                "danger_text": "#ff2b2b", "icon_muted": "#2c2c2c", "icon_user": "#ff4d00",
                "icon_ai": "#ff4d00", "accent": "#ff4d00", "accent_hover": "#ff6a2b",
                "danger": "#ff2b2b", "danger_hover": "#ff5555", "border": "#2c2c2c",
                "border_light": "#3d3d3d", "purple": "#b45cff", "amber": "#ffce4d",
                "switch_off": "#3d3d3d", "blue": "#00b7ff", "blue_text": "#7fd8ff",
                "spinner": "#ff4d00", "focus_ring": "#ff4d00", "preview_bg": "#101010",
            },
            "fonts": {
                "sans": "ui-monospace, 'JetBrains Mono', Menlo, monospace",
                "mono": "ui-monospace, 'JetBrains Mono', Menlo, monospace",
            },
            "radius": {
                "default": "0", "sm": "0", "md": "0", "lg": "0",
                "xl": "0", "2xl": "0", "full": "0",
            },
            "shadows": {
                "xl": "none", "2xl": "none",
            },
            "typography": {
                "xs": "0.75rem", "sm": "0.875rem", "base": "1rem", "lg": "1.125rem",
                "xl": "1.25rem", "2xl": "1.5rem",
            },
            "weights": {"medium": 600, "semibold": 700, "bold": 800},
            "motion": {
                "duration": "100ms", "ease": "steps(2)",
                "spin": "1s", "pulse": "2s",
            },
            "layout": {
                "sidebar": "16rem", "chat_max": "60rem", "panel_max": "48rem",
                "dropdown": "16rem", "bubble_max": "80%", "side_panel": "400px",
                "avatar": "2rem", "message_gap": "1rem", "preview_height": "24rem",
                "auth_max": "24rem", "z_modal": 50,
            },
        },
        "components": {
            "button": {"border-width": "2px", "border-style": "solid", "border-color": "#ff4d00"},
            "bubble": {"border-width": "1px", "border-style": "solid", "border-color": "#2c2c2c"},
        },
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
