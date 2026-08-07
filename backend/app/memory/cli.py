"""CLI for manual memory maintenance.

Usage (from backend/)::

    python -m app.memory.cli status [--user N|--all]
    python -m app.memory.cli consolidate [--user N|--all]
    python -m app.memory.cli lint [--user N|--all]
    python -m app.memory.cli reset [--user N] [--yes]

``reset`` permanently deletes a user's memory store (including raw inbox
transcripts). The inner git history keeps compiled-wiki history; purging that
history requires manual ``git filter-branch`` inside the store — documented in
the README.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from .store import Store
from ..config import settings


async def run(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.memory.cli", description="LLMDash memory maintenance")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("status", "consolidate", "lint"):
        p = sub.add_parser(name)
        _add_user_args(p)
    p = sub.add_parser("reset")
    _add_user_args(p)
    p.add_argument("--yes", action="store_true", help="confirm the wipe")

    args = parser.parse_args(argv)
    store = Store(settings.memory_dir)

    user_ids = _resolve_users(store, getattr(args, "user", None), getattr(args, "all", False))

    if args.cmd == "status":
        for uid in user_ids:
            _print_status(store, uid)
        return 0

    if args.cmd == "consolidate":
        from .consolidate import consolidate

        for uid in user_ids:
            report = await consolidate(store, uid)
            print(f"user {uid}: {report['atoms_before']} -> {report['atoms_after']} atoms, "
                  f"{report['merged']} merged, {report['superseded']} superseded, "
                  f"{report['promoted']} promoted, +{report['persona_applied']} persona")
        return 0

    if args.cmd == "lint":
        from .lint import lint

        for uid in user_ids:
            print(f"--- lint user {uid} ---")
            for f in lint(store, uid):
                print(f"  [{f['severity']}] {f['check']}: {f['detail']}")
        return 0

    # reset
    if not args.yes:
        print("Refusing to reset without --yes (this permanently deletes memory, including raw transcripts).", file=sys.stderr)
        return 2
    for uid in user_ids:
        if store.reset_user(uid):
            print(f"user {uid}: memory store deleted")
        else:
            print(f"user {uid}: no memory store")
    return 0


def _add_user_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--user", type=int, help="user id")
    p.add_argument("--all", action="store_true", help="all users")


def _resolve_users(store: Store, user: Optional[int], all_users: bool) -> list[int]:
    if user is not None:
        return [user]
    if all_users:
        return store.users()
    return store.users()[:1] if store.users() else []


def _print_status(store: Store, user_id: int) -> None:
    if not store.user_exists(user_id):
        print(f"user {user_id}: no memory store")
        return
    scores = store.read_scores(user_id)
    n_atoms = len(scores.get("atoms", {}))
    confirmed = sum(1 for a in scores.get("atoms", {}).values() if a.get("confirmed"))
    pages = len(store.list_pages(user_id))
    inbox = store.count_inbox(user_id)
    log = store.read_log(user_id)
    last = log.strip().splitlines()[-1] if log.strip() else "(no activity)"
    print(
        f"user {user_id}: {n_atoms} atoms ({confirmed} confirmed), {len(scores.get('scenarios', {}))} scenarios, "
        f"{pages} pages, {inbox} inbox items pending\n  last log: {last}"
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
