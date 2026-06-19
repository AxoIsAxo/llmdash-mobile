from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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

    @abstractmethod
    async def execute(self, arguments: dict, **context) -> str:
        ...

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
