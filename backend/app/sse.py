from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

active_generations: dict[int, dict] = {}


def push_to_queues(conv_id: int, event: str):
    gen = active_generations.get(conv_id)
    if gen:
        for q in list(gen.get("queues", [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


def cleanup_generation(conv_id: int):
    gen = active_generations.pop(conv_id, None)
    if gen:
        for q in gen.get("queues", []):
            try:
                q.put_nowait("data: [DONE]\n\n")
            except Exception:
                pass
