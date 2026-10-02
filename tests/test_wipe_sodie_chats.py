"""FK-safe wipe of Sodie threads and messages."""

from __future__ import annotations

import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import (
    SodieActionProposal,
    SodieMessage,
    SodieThread,
    UserPantryItem,
)


def _load_wipe_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "wipe_sodie_chats.py"
    spec = spec_from_file_location("wipe_sodie_chats", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wipe_sodie_chats_keeps_user_and_pantry(db, test_user):
    wipe = _load_wipe_module()

    thread = SodieThread(
        id=uuid.uuid4(),
        user_id=test_user.id,
        scope="global",
        is_temporary=False,
    )
    db.add(thread)
    db.flush()
    db.add(
        SodieMessage(
            id=uuid.uuid4(),
            thread_id=thread.id,
            sender="user",
            content="hello",
        )
    )
    proposal = SodieActionProposal(
        id=uuid.uuid4(),
        user_id=test_user.id,
        thread_id=thread.id,
        action_type="propose_recipe_edit",
        status="pending",
        payload_json="{}",
        diff_json="{}",
        idempotency_key="test-key",
        source_content_hash="abc",
    )
    db.add(proposal)
    db.add(
        UserPantryItem(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name="Salt",
        )
    )
    db.flush()

    before = wipe.table_counts(db)
    assert before["sodie_messages"] == 1
    assert before["sodie_threads"] == 1
    assert before["sodie_action_proposals.thread_id"] == 1

    wipe.wipe_sodie_chats(db, dry_run=False, commit=False)

    after = wipe.table_counts(db)
    assert after["sodie_messages"] == 0
    assert after["sodie_threads"] == 0
    assert after["sodie_action_proposals.thread_id"] == 0
    assert db.query(SodieActionProposal).filter(SodieActionProposal.id == proposal.id).count() == 1
    assert db.get(SodieActionProposal, proposal.id).thread_id is None
    assert db.query(UserPantryItem).filter(UserPantryItem.user_id == test_user.id).count() == 1


def test_wipe_sodie_dry_run_does_not_delete(db, test_user):
    wipe = _load_wipe_module()
    thread = SodieThread(
        id=uuid.uuid4(),
        user_id=test_user.id,
        scope="global",
        is_temporary=False,
    )
    db.add(thread)
    db.flush()
    db.add(
        SodieMessage(
            id=uuid.uuid4(),
            thread_id=thread.id,
            sender="user",
            content="stays",
        )
    )
    db.flush()

    before = wipe.table_counts(db)
    returned = wipe.wipe_sodie_chats(db, dry_run=True, commit=False)
    assert returned["sodie_messages"] == before["sodie_messages"]
    assert wipe.table_counts(db)["sodie_messages"] == before["sodie_messages"]
