"""Chat-command interception for memory maintenance.

Only anchored commands trigger maintenance runs — "should I consolidate my
memory?" is a question, not a command. When a command matches, the runtime
runs the operation before the model sees the turn and injects the report as
the [MEMORY] block; the model narrates it, it never touches the files.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from . import config as mem_cfg
from .store import Store


def match_command(message: str) -> Optional[str]:
    """Return 'consolidate' or 'lint' if the message is an anchored command."""
    m = re.match(mem_cfg.MAINTENANCE_COMMAND_RE, (message or "").strip(), re.IGNORECASE)
    return m.group(1).lower() if m else None


async def maybe_run_command(store: Store, user_id: int, message: str) -> Optional[str]:
    """Run the maintenance op if the message is an anchored command.

    Returns a formatted report for injection, or None when it was not a
    command (normal chat proceeds with regular memory injection).
    """
    cmd = match_command(message)
    if cmd is None:
        return None
    if cmd == "consolidate":
        from .consolidate import consolidate

        # consolidate is pure file work (plus best-effort git) — run it off
        # the event loop so a chat command never freezes other requests.
        report = await asyncio.to_thread(consolidate, store, user_id)
        return (
            f"## Memory maintenance report (consolidate)\n"
            f"- atoms: {report['atoms_before']} -> {report['atoms_after']}\n"
            f"- merged duplicates: {report['merged']}\n"
            f"- superseded contradictions: {report['superseded']}\n"
            f"- promoted: {report['promoted']}\n"
            f"- persona lines applied: {report['persona_applied']}\n"
            f"Index and log updated."
        )
    from .lint import lint

    findings = lint(store, user_id)
    lines = [f"- [{f['severity']}] {f['check']}: {f['detail']}" for f in findings]
    return "## Memory maintenance report (lint)\n" + "\n".join(lines)
