from __future__ import annotations

import inspect
from typing import Optional

from .base import Skill


class SkillRegistry:
    """Single overviewable source of skills (P11).

    Skills are listable by source / category / permission scope, filtered per
    user (entitlements + per-user disable set), and execute under scope
    enforcement (a skill may only run when its declared scopes are allowed).
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def unregister(self, name: str):
        self._skills.pop(name, None)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def list_skills(
        self,
        source: str | None = None,
        category: str | None = None,
        scope: str | None = None,
    ) -> list[Skill]:
        """All registered skills, optionally filtered by manifest fields."""
        out = []
        for skill in self._skills.values():
            if source and skill.source != source:
                continue
            if category and skill.category != category:
                continue
            if scope and scope not in getattr(skill, "scopes", ()):
                continue
            out.append(skill)
        return out

    def get_tool_definitions(
        self,
        entitlements: dict | None = None,
        disabled_skills: set[str] | None = None,
    ) -> list[dict]:
        """Tool defs the model may see for a given user.

        Filters: skill-level enabled flag, P9 entitlements (when provided),
        and the user's per-skill disable set (when provided)."""
        defs = []
        for skill in self._skills.values():
            if getattr(skill, "enabled", True) is False:
                continue
            if entitlements and skill.entitlement and not entitlements.get(skill.entitlement, True):
                continue
            if disabled_skills and skill.name in disabled_skills:
                continue
            defs.append(skill.to_tool_def())
        return defs

    def entitlement_of(self, name: str) -> Optional[str]:
        skill = self._skills.get(name)
        return getattr(skill, "entitlement", None) if skill else None

    def scopes_of(self, name: str) -> tuple[str, ...]:
        skill = self._skills.get(name)
        return tuple(getattr(skill, "scopes", ())) if skill else ()

    def builtin_scope_union(self) -> set[str]:
        """Union of every scope declared by builtin-source skills.

        P11 scope guard: marketplace skills (P12) may only execute with scopes
        that trusted builtins already use — a marketplace skill cannot declare
        a brand-new scope to self-grant capabilities."""
        out: set[str] = set()
        for skill in self._skills.values():
            if skill.source == "builtin":
                out.update(getattr(skill, "scopes", ()))
        return out

    async def execute(
        self,
        name: str,
        arguments: dict,
        *,
        allowed_scopes: set[str] | None = None,
        **context,
    ) -> str:
        skill = self._skills.get(name)
        if not skill:
            return f"Error: Unknown tool '{name}'."

        if getattr(skill, "enabled", True) is False:
            return f"Error: Tool '{name}' is disabled."

        # P11 scope enforcement: a skill may only run when its declared scopes
        # are allowed by the caller (marketplace installs restrict this; the
        # trusted builtins run with no restriction).
        declared = set(getattr(skill, "scopes", ()))
        if allowed_scopes is not None and not declared.issubset(allowed_scopes):
            return (
                f"__TOOL_ERROR__: Tool '{name}' requires scopes "
                f"{sorted(declared)} which are not allowed. "
                f"Allowed scopes: {sorted(allowed_scopes)}."
            )

        sig = inspect.signature(skill.execute)
        filtered_context = {k: v for k, v in context.items() if k in sig.parameters}

        try:
            result = await skill.execute(arguments, **filtered_context)
            result_str = str(result)
            if result_str.startswith(("Error:", "Search failed:", "Request timed out:", "Could not connect:", "Scrape error:", "Failed to fetch")):
                result_str = "__TOOL_ERROR__: " + result_str
            return result_str
        except TypeError as e:
            try:
                type_hints = {k: v for k, v in skill.execute.__annotations__.items() if not k.startswith("_")}
            except Exception:
                type_hints = {}
            params = ", ".join(
                f"{p.name}: {_format_type(type_hints.get(p.name, p.annotation))}"
                for p in sig.parameters.values()
                if not p.name.startswith("_")
            )
            provided = ", ".join(arguments.keys()) or "(none)"
            return (
                f"__TOOL_ERROR__: Tool execution error: {e}\n"
                f"Function signature: {name}({params})\n"
                f"You provided arguments: {{{provided}}}\n"
                f"If the argument you intended to send is large or contains special "
                f"characters, try sending it as a shorter, properly-escaped JSON string.\n"
                f"Please retry with the correct arguments matching the signature above."
            )
        except Exception as e:
            return f"__TOOL_ERROR__: Tool execution error: {e}"


def _format_type(annotation) -> str:
    if annotation is inspect.Parameter.empty or annotation is None:
        return "any"
    if isinstance(annotation, str):
        return annotation
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        origin_name = getattr(origin, "__name__", None) or str(origin)
        args = getattr(annotation, "__args__", ())
        if args:
            inner = ", ".join(_format_type(a) for a in args)
            return f"{origin_name}[{inner}]"
        return origin_name
    return "any"


skill_registry = SkillRegistry()
