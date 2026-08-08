from __future__ import annotations

from ..base import Skill
from ...sandbox import run_in_alpine


class RunCommandSkill(Skill):
    name = "run_command"
    entitlement = "sandbox"
    description = (
        "Execute Linux commands in a secure Alpine Linux sandbox via Docker. Root access, "
        "network enabled. The sandbox is PERSISTENT for this whole conversation: anything "
        "you install (apk add, pip install, npm install) and any files you create stay "
        "available for later commands in this chat. Pre-installed: python3, pip, node, npm, "
        "git, curl, wget, gcc, build-base."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command(s) to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
        },
        "required": ["command"],
    }

    async def execute(self, arguments: dict, _conversation_id: int = 0) -> str:
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", 30)
        result = await run_in_alpine(command, timeout=timeout, conversation_id=_conversation_id)
        return result
