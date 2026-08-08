from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SkillMetadata:
    name: str
    description: str
    source: str = "builtin"
    enabled: bool = True


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

    @abstractmethod
    async def execute(self, arguments: dict, **context) -> str:
        ...

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
