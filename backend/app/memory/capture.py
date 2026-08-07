"""L0 capture — deterministic transcript capture.

These functions are called from the chat request path itself (after the user
message is persisted, and in the assistant finalize path). They never call an
LLM and never let the model decide anything: if the model refuses, errors, or
is cancelled, the transcript is still captured. Every failure is logged and
swallowed — chat must never break because memory capture failed.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .store import Store

logger = logging.getLogger(__name__)

_IMPORTANT_RE = re.compile(
    r"\b(remember|important|don'?t forget|do not forget|note that|please note)\b",
    re.IGNORECASE,
)


def assistant_turn_summary(
    content: str,
    draft_content: str,
    all_tool_calls: Optional[list[dict]],
) -> str:
    """Pick what to capture for an assistant turn, in priority order:

    1. the streamed text (normal replies);
    2. the finalized draft text (error/partial replies — the error message or
       NO_ANSWER_NOTE lives on the draft, not in the stream);
    3. a compact tool-round summary when the turn only executed tools.
    Returns "" when there is genuinely nothing to capture.
    """
    if content:
        return content
    if draft_content:
        return draft_content
    if all_tool_calls:
        names = [(tc.get("name") or "tool") for tc in all_tool_calls if isinstance(tc, dict)]
        return f"[tool round: {', '.join(names) or 'executed tools'} — no closing text]"
    return ""


def capture_user_message(
    store: Store,
    user_id: int,
    *,
    message: str,
    conversation_id: int,
    model: str,
    attachments: Optional[list[dict]] = None,
) -> Optional[str]:
    """Persist one user message to inbox/ as an L0 raw transcript.

    Runs before the model call, so capture works even if the model refuses or
    behaves badly. Returns the source id, or None if capture failed.
    """
    try:
        content = (message or "").strip()
        if attachments:
            names = [a.get("filename", "") for a in attachments if a.get("filename")]
            if names:
                content = f"{content}\n[attachments: {', '.join(names)}]" if content else f"[attachments: {', '.join(names)}]"
        return store.write_inbox(
            user_id,
            role="user",
            content=content,
            conversation_id=conversation_id,
            model=model,
            important=bool(_IMPORTANT_RE.search(message or "")),
        )
    except Exception as exc:
        logger.warning("memory: user-message capture failed for user %s: %s", user_id, exc)
        return None


def capture_assistant_reply(
    store: Store,
    user_id: int,
    *,
    content: str,
    conversation_id: int,
    model: str,
    status: str = "done",
) -> Optional[str]:
    """Persist one assistant reply to inbox/ (from the finalize path).

    ``status`` reflects what happened: done / cancelled / interrupted — so
    extraction can weight partial or cancelled turns accordingly.
    """
    if not content:
        return None  # nothing produced — nothing to capture
    try:
        return store.write_inbox(
            user_id,
            role="assistant",
            content=content,
            conversation_id=conversation_id,
            model=model,
            important=False,
            status=status,
        )
    except Exception as exc:
        logger.warning("memory: assistant-reply capture failed for user %s: %s", user_id, exc)
        return None
