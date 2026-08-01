"""Shared text-patching helpers used by the CSS tools and the document skill."""

import re


def normalize_ws_collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def trim_per_line(s: str) -> str:
    return "\n".join(line.strip() for line in s.split("\n"))


def count_and_replace(haystack: str, needle: str, replacement: str) -> tuple[int, str]:
    """Returns (count, replaced) where replaced is haystack with the first match
    swapped in (only if count==1)."""
    count = haystack.count(needle)
    if count != 1:
        return count, haystack
    return count, haystack.replace(needle, replacement, 1)


def try_patch_strategies(current: str, old_str: str, new_str: str) -> tuple[str, str]:
    """Try matching strategies in order: exact, trim-per-line, whitespace-collapse.
    Returns (outcome, new_css). outcome is one of: 'ok', 'no_match', 'ambiguous'.
    On 'ok', new_css holds the patched string. On other outcomes, new_css is the
    original current."""
    if not old_str:
        return "no_match", current

    ambiguous = False

    count, replaced = count_and_replace(current, old_str, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    cur_trimmed = trim_per_line(current)
    old_trimmed = trim_per_line(old_str)
    count, replaced = count_and_replace(cur_trimmed, old_trimmed, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    cur_collapsed = normalize_ws_collapse(current)
    old_collapsed = normalize_ws_collapse(old_str)
    count, replaced = count_and_replace(cur_collapsed, old_collapsed, new_str)
    if count == 1:
        return "ok", replaced
    if count > 1:
        ambiguous = True

    return ("ambiguous" if ambiguous else "no_match"), current


def apply_patch(current: str, old_str: str, new_str: str) -> tuple[bool, str, str]:
    """Returns (success, error_message, new_css). error_message set on failure."""
    if not old_str:
        return False, "old_str is empty — cannot patch", current
    if current == "":
        return False, "no match found — user CSS is empty. Did you call get_user_css first?", current

    outcome, new_css = try_patch_strategies(current, old_str, new_str)
    if outcome == "ok":
        return True, "", new_css
    if outcome == "ambiguous":
        return False, "ambiguous match — add more context lines to old_str", current

    preview_lines = old_str.split("\n")[:3]
    preview = "\n".join(preview_lines)
    return False, f"no match found. First lines of old_str:\n{preview}", current
