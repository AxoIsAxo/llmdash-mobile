from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SkillMetadata:
    """P11 — skill manifest (self-contained, inspectable module metadata)."""
    name: str
    description: str
    source: str = "builtin"          # builtin | user | marketplace
    enabled: bool = True
    version: str = "1.0.0"
    author: str = "LLMDash"
    category: str = "general"
    scopes: tuple[str, ...] = ()
    entitlement: Optional[str] = None


@dataclass
class SkillResult:
    success: bool
    content: str
    error: Optional[str] = None


class Skill(ABC):
    name: str
    description: str
    input_schema: dict
    enabled: bool = True
    # P9: entitlement key (see entitlements.py) this skill is gated by.
    # None = always available regardless of plan.
    entitlement: Optional[str] = None
    # P11: manifest fields. `source` distinguishes builtin / user / marketplace
    # skills; `scopes` declare which capabilities a skill may touch (git, web,
    # files, sandbox, theme, render, ...) and are enforced at execution time
    # (a skill that declares no `sandbox` scope cannot run run_command etc.).
    version: str = "1.0.0"
    author: str = "LLMDash"
    source: str = "builtin"
    category: str = "general"
    scopes: tuple[str, ...] = ()

    @abstractmethod
    async def execute(self, arguments: dict, **context) -> str:
        ...

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_manifest(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "version": self.version,
            "author": self.author,
            "source": self.source,
            "category": self.category,
            "scopes": list(self.scopes),
            "entitlement": self.entitlement,
        }
