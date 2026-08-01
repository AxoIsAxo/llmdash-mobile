from __future__ import annotations

import base64
import json

from sqlalchemy import select

from ..base import Skill
from ...database import User, async_session
from ...theme import (
    DEFAULT_SPEC, get_preset, normalize_theme_patch,
    theme_spec_to_css, validate_raw_css, validate_theme_spec,
)


def _encode_new_css_marker(new_css: str) -> str:
    return "NEW_CSS:" + base64.b64encode(new_css.encode("utf-8")).decode("ascii")


def _current_spec(user) -> dict:
    if user.theme_spec:
        try:
            return json.loads(user.theme_spec)
        except json.JSONDecodeError:
            pass
    return json.loads(json.dumps(DEFAULT_SPEC))


class GetThemeSkill(Skill):
    name = "get_theme"
    description = (
        "Get the user's current UI theme as a structured JSON spec (tokens + components). "
        "This is the PRIMARY way to read the theme state. The spec has 'tokens' (colors, fonts, "
        "radius, shadows, typography, weights, motion, layout) and 'components' (per-part "
        "overrides). ALWAYS call this before any theme edit so you operate on the real current "
        "state. Token values are validated server-side: colors as #hex or CSS names, lengths as "
        "px/rem/% etc."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        if not _current_user:
            return "Error: Not authenticated"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"
            spec = _current_spec(user)
        return json.dumps(spec, indent=2)


class PatchThemeSkill(Skill):
    name = "patch_theme"
    description = (
        "Apply a JSON merge patch to the user's theme spec and save it. This is the PRIMARY "
        "theme editing tool — use it for ANY style/layout change (colors, fonts, radii, shadows, "
        "spacing, sidebar width, bubble width, preview panel size, animations, component "
        "overrides like glassmorphism). Patch shape: {\"tokens\": {\"colors\": {\"accent\": "
        "\"#ff5c8a\"}, \"layout\": {\"sidebar\": \"320px\"}}, \"components\": {\"sidebar\": "
        "{\"background\": \"rgb(18 23 39 / 0.6)\", \"backdrop-filter\": \"blur(18px)\"}}}. "
        "Setting a key to null removes it. You may also pass \"preset\": \"default|compact|"
        "glassmorphism|brutalism\" to start from a preset. Values are validated server-side; "
        "invalid values return an error listing what to fix. Always call get_theme first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "object",
                "description": "JSON merge patch applied to the current theme spec (tokens/components/preset).",
            },
        },
        "required": ["patch"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        if not _current_user:
            return "Error: Not authenticated"
        patch = arguments.get("patch")
        if not isinstance(patch, dict):
            return "Error: 'patch' must be a JSON object"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"
            base = _current_spec(user)
            try:
                normalized, errors = normalize_theme_patch(base, patch)
                if errors:
                    return f"Error: {'; '.join(errors[:8])}"
                css = theme_spec_to_css(normalized)
                user.theme_spec = json.dumps(normalized, separators=(",", ":"))
                user.custom_css = css or None
                await sess.commit()
            except Exception as e:
                return f"Error: {e}"
        return f"Theme updated.\n{_encode_new_css_marker(css)}"


class ResetThemeSkill(Skill):
    name = "reset_theme"
    description = (
        "Reset the user's theme to a built-in preset. Presets: 'default' (original LLMDash dark "
        "theme), 'compact' (denser UI), 'glassmorphism' (frosted translucent surfaces), "
        "'brutalism' (sharp corners, high contrast). Use this when the user asks to reset the "
        "theme, or to switch to a named style. For custom changes use patch_theme."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "description": "Preset name: default, compact, glassmorphism, or brutalism",
            },
        },
        "required": ["preset"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        if not _current_user:
            return "Error: Not authenticated"
        preset = str(arguments.get("preset", "default")).strip().lower()
        preset_spec = get_preset(preset)
        if preset_spec is None:
            return f"Error: unknown preset '{preset}'. Available: default, compact, glassmorphism, brutalism"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"
            try:
                normalized, errors = validate_theme_spec(preset_spec)
                if errors:
                    return f"Error: {'; '.join(errors[:8])}"
                css = theme_spec_to_css(normalized)
                user.theme_spec = json.dumps(normalized, separators=(",", ":"))
                user.custom_css = css or None
                await sess.commit()
            except Exception as e:
                return f"Error: {e}"
        return f"Theme reset to '{preset}'.\n{_encode_new_css_marker(css)}"


class GetUserCssSkill(Skill):
    name = "get_user_css"
    description = "Get the current user's custom stylesheet. Returns the FULL CSS string the user has saved (or empty string if none). This stylesheet controls the entire LLMDash UI — not just colors. It can contain rules for colors, backgrounds, borders, border-radius, shadows, spacing, font family/size/weight, line-height, opacity, transitions, animations, layout widths, z-index, and any other CSS property. ALWAYS call this first before any styling edit so you operate on the real current state, not a stale memory."
    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        if not _current_user:
            return "Error: Not authenticated"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if user and user.custom_css:
                return user.custom_css
            return ""


class PatchUserCssSkill(Skill):
    name = "patch_user_css"
    description = "Make a targeted edit to the user's custom stylesheet by finding an exact block of text and replacing it. Use this for advanced raw-CSS tweaks that the theme tokens don't cover. For most styling work prefer patch_theme (structured, validated). Always call get_user_css first so old_str matches the real current state. Provide enough surrounding lines in old_str to make it unique. Matching tries (in order): 1) exact match, 2) trim leading/trailing whitespace per line, 3) collapse all whitespace runs to single space. On success, saves server-side and applies immediately. Note: url(), @import, @document, expression(), -moz-binding, behavior: and other at-rules except @media/@supports/@keyframes are blocked for security."
    input_schema = {
        "type": "object",
        "properties": {
            "old_str": {"type": "string", "description": "The exact block of CSS to find. Include enough surrounding lines (a few lines before and after) to make it unique in the current stylesheet."},
            "new_str": {"type": "string", "description": "The replacement CSS block. Pass an empty string to delete the matched block."},
            "description": {"type": "string", "description": "Optional one-line description of what this patch changes (for your own reasoning; not displayed to the user)."},
        },
        "required": ["old_str", "new_str"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        from ...patch_utils import apply_patch

        old_str = arguments.get("old_str", "")
        new_str = arguments.get("new_str", "")
        if not _current_user:
            return "Error: Not authenticated"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"

            current = user.custom_css or ""
            success, err, new_css = apply_patch(current, old_str, new_str)
            if not success:
                return f"Error: {err}"

            ok, verr = validate_raw_css(new_css)
            if not ok:
                return f"Error: blocked CSS: {verr}"

            user.custom_css = new_css or None
            await sess.commit()
            return f"patched successfully\n{_encode_new_css_marker(new_css)}"


class AppendUserCssSkill(Skill):
    name = "append_user_css"
    description = "Append new CSS rules to the END of the user's custom stylesheet. Use this for advanced raw-CSS additions not covered by theme tokens (prefer patch_theme for most work). Saves server-side and applies immediately. url(), @import, @document, expression(), -moz-binding, behavior: and at-rules other than @media/@supports/@keyframes are blocked for security."
    input_schema = {
        "type": "object",
        "properties": {
            "css": {"type": "string", "description": "The CSS rules to append. Do not include existing rules — only the new ones."},
        },
        "required": ["css"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        css = arguments.get("css", "")
        if not _current_user:
            return "Error: Not authenticated"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"

            current = user.custom_css or ""
            if current and not current.endswith("\n"):
                current += "\n"
            if current and not current.endswith("\n\n"):
                current += "\n"
            new_css = current + css
            if not new_css.endswith("\n"):
                new_css += "\n"

            ok, verr = validate_raw_css(new_css)
            if not ok:
                return f"Error: blocked CSS: {verr}"

            user.custom_css = new_css or None
            await sess.commit()
            return f"appended successfully\n{_encode_new_css_marker(new_css)}"


class SetUserCssSkill(Skill):
    name = "set_user_css"
    description = "FULL REPLACEMENT of the user's custom stylesheet. Do NOT use this for targeted edits — use patch_user_css instead. Only call this when the user explicitly asks to 'reset', 'completely redo', or 'overwrite' all styles. The replacement can target any CSS property (colors, fonts, spacing, borders, layout, animations, etc.), not just colors. Pass the COMPLETE CSS string (including any existing styles you want to keep)."
    input_schema = {
        "type": "object",
        "properties": {
            "css": {"type": "string", "description": "The complete CSS string to set as the user's custom CSS."},
        },
        "required": ["css"],
    }

    async def execute(self, arguments: dict, _current_user: dict = None) -> str:
        css = arguments.get("css", "")
        if not _current_user:
            return "Error: Not authenticated"
        ok, verr = validate_raw_css(css)
        if not ok:
            return f"Error: blocked CSS: {verr}"
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"
            user.custom_css = css or None
            await sess.commit()
            return f"CSS saved successfully ({len(css)} characters)\n{_encode_new_css_marker(css or '')}"
