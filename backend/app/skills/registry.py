from __future__ import annotations

import inspect
from typing import Optional

from .base import Skill


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def unregister(self, name: str):
        self._skills.pop(name, None)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def get_tool_definitions(self) -> list[dict]:
        return [
            skill.to_tool_def()
            for skill in self._skills.values()
            if getattr(skill, "enabled", True)
        ]

    async def execute(self, name: str, arguments: dict, **context) -> str:
        skill = self._skills.get(name)
        if not skill:
            return (
                f"Error: Unknown tool '{name}'. If this skill was created in the "
                f"admin panel, note that database skills have no execution backend "
                f"yet and cannot be invoked."
            )

        if not getattr(skill, "enabled", True):
            return f"Error: Tool '{name}' is disabled."

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
