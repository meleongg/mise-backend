#!/usr/bin/env python3
"""
Wipe Sodie chat history (messages + threads) after a catalog reseed.

FK-safe: clears sodie_action_proposals.thread_id, then messages, then threads.
Does not delete users, user_pantry_items, or catalog tables.

Requires DATABASE_URL (e.g. backend/.env).

Examples:
  python scripts/wipe_sodie_chats.py --dry-run
  python scripts/wipe_sodie_chats.py --force
"""

from __future__ import annotations

import argparse
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SodieActionProposal, SodieMessage, SodieThread

load_dotenv(os.path.join(project_root, ".env"))

WIPE_STEPS: list[tuple[str, type]] = [
    ("sodie_messages", SodieMessage),
    ("sodie_threads", SodieThread),
]


def table_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, model in WIPE_STEPS:
        counts[label] = int(session.scalar(select(func.count()).select_from(model)) or 0)
    linked = int(
        session.scalar(
            select(func.count())
            .select_from(SodieActionProposal)
            .where(SodieActionProposal.thread_id.is_not(None))
        )
        or 0
    )
    counts["sodie_action_proposals.thread_id"] = linked
    return counts


def wipe_sodie_chats(
    session: Session, *, dry_run: bool = False, commit: bool = True
) -> dict[str, int]:
    """
    Delete Sodie messages/threads; null proposal thread_ids first.

    Returns pre-delete counts (including proposals with a thread_id).
    """
    before = table_counts(session)
    if dry_run:
        return before

    session.execute(
        update(SodieActionProposal)
        .where(SodieActionProposal.thread_id.is_not(None))
        .values(thread_id=None)
    )
    for _label, model in WIPE_STEPS:
        session.execute(delete(model))
    if commit:
        session.commit()
    else:
        session.flush()
    return before


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wipe Sodie threads and messages. "
            "Keeps users and pantry; nulls proposal thread_ids."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only; do not delete",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation (required for non-dry-run in CI)",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        counts = table_counts(session)
        print("Sodie chat tables (will wipe):")
        for label, _model in WIPE_STEPS:
            print(f"  {label}: {counts[label]}")
        print(
            f"  sodie_action_proposals.thread_id to null: "
            f"{counts['sodie_action_proposals.thread_id']}"
        )
        print("Preserved: users, user_pantry_items, recipes/catalog")

        if args.dry_run:
            print("(dry-run: no deletes)")
            return 0

        if not args.force:
            response = input(
                "⚠️  Delete all Sodie threads/messages? Type 'wipe' to confirm: "
            )
            if response.strip() != "wipe":
                print("Cancelled.")
                return 0

        deleted = wipe_sodie_chats(session, dry_run=False)
        after = table_counts(session)
        print("Deleted / cleared:")
        for label, _model in WIPE_STEPS:
            print(f"  {label}: {deleted[label]} → {after[label]}")
        print(
            "  sodie_action_proposals.thread_id: "
            f"{deleted['sodie_action_proposals.thread_id']} → "
            f"{after['sodie_action_proposals.thread_id']}"
        )
        if any(after[label] for label, _ in WIPE_STEPS) or after[
            "sodie_action_proposals.thread_id"
        ]:
            print("ERROR: some Sodie wipe targets still have rows", file=sys.stderr)
            return 1
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
