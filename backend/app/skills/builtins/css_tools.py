from __future__ import annotations

import base64

from sqlalchemy import select

from ..base import Skill
from ...database import User, async_session


def _encode_new_css_marker(new_css: str) -> str:
    return "NEW_CSS:" + base64.b64encode(new_css.encode("utf-8")).decode("ascii")


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
    description = "Make a targeted edit to the user's custom stylesheet by finding an exact block of text and replacing it. This is the PRIMARY edit tool and is NOT limited to colors — use it for any CSS change (fonts, spacing, borders, radius, shadows, layout, animations, etc.). Always call get_user_css first so old_str matches the real current state. Provide enough surrounding lines in old_str to make it unique. Matching tries (in order): 1) exact match, 2) trim leading/trailing whitespace per line, 3) collapse all whitespace runs to single space. On success, saves server-side and applies immediately."
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
        from ...tools import _apply_patch

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
            success, err, new_css = _apply_patch(current, old_str, new_str)
            if not success:
                return f"Error: {err}"

            user.custom_css = new_css or None
            await sess.commit()
            return f"patched successfully\n{_encode_new_css_marker(new_css)}"


class AppendUserCssSkill(Skill):
    name = "append_user_css"
    description = "Append new CSS rules to the END of the user's custom stylesheet. Use this when adding entirely new rules that don't exist yet. Works for any kind of style — colors, fonts, spacing, borders, radius, shadows, layout, animations, etc. Saves server-side and applies immediately."
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
        async with async_session() as sess:
            result = await sess.execute(select(User).where(User.id == _current_user["user_id"]))
            user = result.scalar_one_or_none()
            if not user:
                return "Error: User not found"
            user.custom_css = css or None
            await sess.commit()
            return f"CSS saved successfully ({len(css)} characters)\n{_encode_new_css_marker(css or '')}"
