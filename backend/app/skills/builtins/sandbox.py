from __future__ import annotations

from ..base import Skill
from ...sandbox import run_in_alpine


class RunCommandSkill(Skill):
    name = "run_command"
    description = "Execute Linux commands in a secure Alpine Linux sandbox via Docker. Has network access and common tools (curl, wget, python3, node, git)."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command(s) to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
        },
        "required": ["command"],
    }

    async def execute(self, arguments: dict) -> str:
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", 30)
        result = await run_in_alpine(command, timeout=timeout)
        return result
