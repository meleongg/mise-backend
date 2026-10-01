"""Regression coverage for Sodie recipe proposals and personal recipes."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PersonalRecipe, User
from app.utils.auth import get_current_user
from tests.conftest import app, override_get_current_user
import app.utils.password as password_utils

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "d1e2f3a4_personal_recipes_and_proposals.py"
)
_spec = spec_from_file_location("d1e2f3a4_personal_recipes_and_proposals", _MIGRATION_PATH)
assert _spec is not None and _spec.loader is not None
migration = module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _as_user(user_id: uuid.UUID):
    def override(db_session: Session = Depends(get_db)) -> User:
        return db_session.query(User).filter(User.id == user_id).one()

    return override


def _make_user(email: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        first_name="Other",
        last_name="User",
        cuisine="Italian",
        frequency=3,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password=password_utils.hash_password("OtherUser123!"),
    )


def _propose_body(recipe_id: str, *, key: str, title: str = "Spicy Pasta"):
    return {
        "source_recipe_id": recipe_id,
        "idempotency_key": key,
        "rationale": "Make it punchier for weeknight cooking.",
        "patch": {
            "title": title,
            "notes": "Add chili flakes to taste.",
            "servings": "4 servings",
        },
    }


def test_migration_revises_rls_head_and_enables_rls(monkeypatch):
    assert migration.down_revision == "c7d8e9f0"
    assert migration.revision == "d1e2f3a4"
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda sql: statements.append(str(sql)))
    monkeypatch.setattr(migration.op, "create_table", lambda *a, **k: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *a, **k: None)
    migration.upgrade()
    assert statements == [
        "ALTER TABLE personal_recipes ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE personal_recipe_revisions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE sodie_action_proposals ENABLE ROW LEVEL SECURITY",
    ]


def test_approve_creates_personal_lineage_without_mutating_catalog(
    client, db: Session, test_user: User, test_recipes: list
):
    recipe = test_recipes[0]
    original_name = recipe.name
    original_ingredients = recipe.ingredients

    created = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(recipe.id), key="approve-lineage-1"),
    )
    assert created.status_code == 200
    proposal_id = created.json()["proposal"]["id"]
    assert created.json()["proposal"]["status"] == "pending"
    assert created.json()["proposal"]["impact"]["shopping_list"] == "unchanged until shopping reconciliation"
    assert created.json()["proposal"]["impact"]["list_reconciliation_queued"] is False

    approved = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "applied"
    assert body["personal_recipe_id"] is not None

    db.refresh(recipe)
    assert recipe.name == original_name
    assert recipe.ingredients == original_ingredients

    personal = (
        db.query(PersonalRecipe)
        .filter(PersonalRecipe.id == uuid.UUID(body["personal_recipe_id"]))
        .one()
    )
    assert personal.user_id == test_user.id
    assert personal.source_recipe_id == recipe.id
    assert personal.name == "Spicy Pasta"
    assert personal.notes == "Add chili flakes to taste."
    assert personal.current_revision == 1
    assert len(personal.revisions) == 1

    listed = client.get("/api/personal-recipes")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["name"] == "Spicy Pasta"


def test_reject_leaves_catalog_and_personal_recipes_unchanged(
    client, db: Session, test_user: User, test_recipes: list
):
    recipe = test_recipes[0]
    created = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(recipe.id), key="reject-key-1", title="Rejected Pasta"),
    )
    proposal_id = created.json()["proposal"]["id"]
    rejected = client.post(f"/api/sodie/proposals/{proposal_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get("/api/personal-recipes").json() == []
    db.refresh(recipe)
    assert recipe.name == "Test Recipe 0"


def test_duplicate_approve_is_idempotent(client, test_user: User, test_recipes: list):
    recipe_id = str(test_recipes[0].id)
    proposal_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(recipe_id, key="idempotent-approve"),
    ).json()["proposal"]["id"]
    first = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    second = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["personal_recipe_id"] == second.json()["personal_recipe_id"]
    assert second.json()["status"] == "applied"
    assert len(client.get("/api/personal-recipes").json()) == 1


def test_second_approve_same_source_bumps_revision_not_duplicate(
    client, db: Session, test_user: User, test_recipes: list
):
    recipe_id = str(test_recipes[0].id)
    first_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(recipe_id, key="upsert-rev-1", title="Cookies v1"),
    ).json()["proposal"]["id"]
    first = client.post(f"/api/sodie/proposals/{first_id}/approve")
    assert first.status_code == 200
    personal_id = first.json()["personal_recipe_id"]

    second_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(recipe_id, key="upsert-rev-2", title="Cookies v2"),
    ).json()["proposal"]["id"]
    second = client.post(f"/api/sodie/proposals/{second_id}/approve")
    assert second.status_code == 200
    assert second.json()["personal_recipe_id"] == personal_id

    listed = client.get("/api/personal-recipes").json()
    assert len(listed) == 1
    assert listed[0]["id"] == personal_id
    assert listed[0]["current_revision"] == 2
    assert listed[0]["name"] == "Cookies v2"


def test_archive_personal_recipe(client, test_user: User, test_recipes: list):
    proposal_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(test_recipes[0].id), key="archive-1"),
    ).json()["proposal"]["id"]
    personal_id = client.post(f"/api/sodie/proposals/{proposal_id}/approve").json()[
        "personal_recipe_id"
    ]
    archived = client.delete(f"/api/personal-recipes/{personal_id}")
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False
    assert client.get("/api/personal-recipes").json() == []


def test_propose_idempotency_key_returns_same_proposal(
    client, test_user: User, test_recipes: list
):
    body = _propose_body(str(test_recipes[0].id), key="same-key-once")
    first = client.post("/api/sodie/proposals", json=body)
    second = client.post("/api/sodie/proposals", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["proposal"]["id"] == second.json()["proposal"]["id"]


def test_stale_proposal_is_rejected_when_catalog_changes(
    client, db: Session, test_user: User, test_recipes: list
):
    recipe = test_recipes[0]
    proposal_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(recipe.id), key="stale-key-1"),
    ).json()["proposal"]["id"]
    recipe.name = "Renamed Catalog Recipe"
    db.flush()
    stale = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()
    assert client.get("/api/personal-recipes").json() == []


def test_proposal_and_personal_recipe_access_denied_across_users(
    client, db: Session, test_user: User, test_recipes: list
):
    proposal_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(test_recipes[0].id), key="cross-user-1"),
    ).json()["proposal"]["id"]
    client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    personal_id = client.get("/api/personal-recipes").json()[0]["id"]

    other = _make_user("other-proposals@example.com")
    db.add(other)
    db.flush()
    app.dependency_overrides[get_current_user] = _as_user(other.id)
    try:
        assert client.get(f"/api/sodie/proposals/{proposal_id}").status_code == 404
        assert client.post(f"/api/sodie/proposals/{proposal_id}/approve").status_code == 404
        assert client.get(f"/api/personal-recipes/{personal_id}").status_code == 404
        assert client.get("/api/personal-recipes").json() == []
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user


def test_plan_schedule_unchanged_on_approve(
    client, db: Session, test_plan, test_recipes: list
):
    before = test_plan.recipe_schedule
    proposal_id = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(test_recipes[0].id), key="plan-unchanged"),
    ).json()["proposal"]["id"]
    assert client.post(f"/api/sodie/proposals/{proposal_id}/approve").status_code == 200
    db.refresh(test_plan)
    assert test_plan.recipe_schedule == before


def test_merge_ingredient_updates_keeps_full_list():
    from app.schemas.sodie_proposals import IngredientLine
    from app.services.sodie_llm import merge_ingredient_updates

    base = [
        {"name": "all-purpose flour", "measure": "2 cups"},
        {"name": "salt", "measure": "1/2 teaspoon"},
        {"name": "chocolate chips", "measure": "2 cups"},
    ]
    merged = merge_ingredient_updates(
        base, [IngredientLine(name="salt", measure="1 teaspoon")]
    )
    assert len(merged) == 3
    assert merged[0]["name"] == "all-purpose flour"
    assert merged[1] == {"name": "salt", "measure": "1 teaspoon"}
    assert merged[2]["name"] == "chocolate chips"


def test_recipe_edit_patch_prompt_routes_taste_to_ingredients():
    from app.services.sodie_llm import RECIPE_EDIT_PATCH_SYSTEM, build_recipe_edit_patch_prompt

    assert "NEVER return only the changed ingredient" in RECIPE_EDIT_PATCH_SYSTEM
    assert "COMPLETE recipe list" in RECIPE_EDIT_PATCH_SYSTEM
    assert "suggest_swap" in RECIPE_EDIT_PATCH_SYSTEM
    assert "out_of_scope" in RECIPE_EDIT_PATCH_SYSTEM
    assert "amount_ambiguous" in RECIPE_EDIT_PATCH_SYSTEM
    messages = build_recipe_edit_patch_prompt(
        {
            "title": "Cookies",
            "ingredients": [{"name": "salt", "measure": "1/4 tsp"}],
            "notes": None,
        },
        "can we make the cookies saltier",
        user_profile={"allergens": ["peanuts"], "dietary_restrictions": []},
    )
    assert "CURRENT RECIPE" in messages[1].content
    assert "USER PROFILE" in messages[1].content
    assert "peanuts" in messages[1].content
    assert "can we make the cookies saltier" in messages[1].content
    assert "PENDING PROPOSAL: none" in messages[1].content


def test_create_proposal_from_request_uses_structured_patch(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    recipe = test_recipes[0]

    def fake_generate(snapshot, request, pending_diff=None, user_profile=None):
        assert "saltier" in request.lower()
        return RecipeEditPatchDraft(
            intent="propose_edit",
            ingredients=[
                IngredientLine(name="flour", measure="2 cups"),
                IngredientLine(name="salt", measure="1 tsp"),
            ],
            change_summary="Increased salt for a saltier cookie.",
            assistant_reply="Here’s a proposal from what you asked for.",
        )

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch", fake_generate
    )

    created = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(recipe.id),
            "request": "can we make the cookies saltier",
            "idempotency_key": "from-request-saltier-1",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "proposal"
    proposal = body["proposal"]
    fields = proposal["diff"]["fields"]
    assert "ingredients" in fields
    assert "notes" not in fields
    assert "salt" in json.dumps(fields["ingredients"]["after"]).lower()


def test_from_request_persists_user_and_ai_on_thread(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            ingredients=[IngredientLine(name="salt", measure="1 tsp")],
            change_summary="More salt",
            assistant_reply="Here’s a saltier proposal.",
        ),
    )
    thread_id = client.post(
        "/api/sodie/threads",
        json={"scope": "recipe", "context_id": str(test_recipes[0].id)},
    ).json()["id"]
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "make it saltier",
            "idempotency_key": "persist-user-ai-1",
            "thread_id": thread_id,
        },
    )
    assert res.status_code == 200
    msgs = client.get(f"/api/sodie/threads/{thread_id}").json()["messages"]
    assert [m["sender"] for m in msgs] == ["user", "ai"]
    assert msgs[0]["content"] == "make it saltier"
    assert "saltier" in msgs[1]["content"].lower()


def test_list_thread_proposals_for_resume(
    client, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            ingredients=[IngredientLine(name="garlic", measure="2 cloves")],
            change_summary="More garlic",
            assistant_reply="Here’s a garlic proposal.",
        ),
    )
    thread_id = client.post(
        "/api/sodie/threads",
        json={"scope": "recipe", "context_id": str(test_recipes[0].id)},
    ).json()["id"]
    created = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "add garlic",
            "idempotency_key": "list-resume-1",
            "thread_id": thread_id,
        },
    )
    assert created.status_code == 200
    proposal_id = created.json()["proposal"]["id"]

    listed = client.get(f"/api/sodie/threads/{thread_id}/proposals")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == proposal_id
    assert listed.json()[0]["status"] == "pending"

    pending_only = client.get(
        f"/api/sodie/threads/{thread_id}/proposals",
        params={"status": "pending"},
    )
    assert pending_only.status_code == 200
    assert len(pending_only.json()) == 1

    other_thread = client.post("/api/sodie/threads", json={"scope": "global"}).json()[
        "id"
    ]
    empty = client.get(f"/api/sodie/threads/{other_thread}/proposals")
    assert empty.status_code == 200
    assert empty.json() == []


def test_create_proposal_from_request_needs_more_info(
    client, db: Session, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="needs_more_info",
            change_summary="Too vague.",
            assistant_reply="What should I change — less sugar, more salt, or something else?",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "make it better somehow",
            "idempotency_key": "from-request-ambiguous-1",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "needs_more_info"
    assert body["proposal"] is None
    assert "sugar" in body["assistant_message"].lower() or "change" in body["assistant_message"].lower()


def test_create_proposal_from_request_coach_qa_falls_through(
    client, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="coach_qa",
            change_summary="Technique question.",
            assistant_reply="ok",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "How long does this bake?",
            "idempotency_key": "from-request-coach-qa-1",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "coach_qa"
    assert body["proposal"] is None


def test_kitchen_thread_rejects_recipe_edit_proposals(
    client, test_user: User, test_recipes: list, monkeypatch
):
    """Kitchen Mode is coach-only — proposal endpoints must not attach edits."""
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    recipe = test_recipes[0]
    kitchen = client.post(
        "/api/sodie/threads",
        json={"scope": "kitchen", "context_id": str(recipe.id)},
    )
    assert kitchen.status_code == 200
    thread_id = kitchen.json()["id"]

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            ingredients=[
                IngredientLine(name="flour", measure="2 cups"),
                IngredientLine(name="salt", measure="1 tsp"),
            ],
            change_summary="Increased salt.",
            assistant_reply="Here’s a proposal.",
        ),
    )

    from_request = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(recipe.id),
            "thread_id": thread_id,
            "request": "make it saltier",
            "idempotency_key": "kitchen-block-from-request-1",
        },
    )
    assert from_request.status_code == 409
    assert "recipe" in from_request.json()["detail"].lower()

    direct = client.post(
        "/api/sodie/proposals",
        json={
            **_propose_body(str(recipe.id), key="kitchen-block-direct-1"),
            "thread_id": thread_id,
        },
    )
    assert direct.status_code == 409
    assert "recipe" in direct.json()["detail"].lower()


def test_shopping_thread_rejects_recipe_edit_proposals(
    client, test_user: User, test_recipes: list, monkeypatch
):
    """Peer leak: non-recipe scopes must not attach recipe-edit proposals."""
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    recipe = test_recipes[0]
    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            ingredients=[IngredientLine(name="salt", measure="1 tsp")],
            change_summary="More salt.",
            assistant_reply="Here’s a proposal.",
        ),
    )

    shopping = client.post("/api/sodie/threads", json={"scope": "shopping"})
    assert shopping.status_code == 200, shopping.text
    thread_id = shopping.json()["id"]

    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(recipe.id),
            "thread_id": thread_id,
            "request": "make it saltier",
            "idempotency_key": "shopping-block-from-request-1",
        },
    )
    assert res.status_code == 409
    assert "recipe" in res.json()["detail"].lower()


def test_non_analytics_thread_rejects_preference_and_pick_proposals(
    client, test_user: User, test_recipes: list, monkeypatch
):
    """Preference tweaks and recipe picks belong on analytics threads only."""
    from app.schemas.sodie_proposals import PreferenceTweakDraft, RecipePickDraft

    recipe_thread = client.post(
        "/api/sodie/threads",
        json={"scope": "recipe", "context_id": str(test_recipes[0].id)},
    )
    assert recipe_thread.status_code == 200
    thread_id = recipe_thread.json()["id"]

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="propose_preference",
            max_cook_time_minutes=40,
            change_summary="Shorter cook time.",
            assistant_reply="Here’s a tweak.",
        ),
    )
    pref = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "thread_id": thread_id,
            "request": "Shorten my max cook time",
            "idempotency_key": "scope-block-pref-1",
        },
    )
    assert pref.status_code == 409
    assert "analytics" in pref.json()["detail"].lower()

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=str(test_recipes[0].id),
            change_summary="A pick.",
            assistant_reply="Here’s a pick.",
        ),
    )
    pick = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "thread_id": thread_id,
            "request": "Suggest an easy pasta",
            "idempotency_key": "scope-block-pick-1",
        },
    )
    assert pick.status_code == 409
    assert "analytics" in pick.json()["detail"].lower()


def test_create_proposal_from_request_clarify_keeps_pending(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipeEditPatchDraft

    recipe = test_recipes[0]
    created = client.post(
        "/api/sodie/proposals",
        json=_propose_body(str(recipe.id), key="clarify-pending-1"),
    )
    proposal_id = created.json()["proposal"]["id"]

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="clarify",
            change_summary="User asked why salt increased.",
            assistant_reply="The pending diff doubles the salt measure; approve to keep it.",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(recipe.id),
            "request": "why did you change the salt?",
            "idempotency_key": "from-request-clarify-1",
            "pending_proposal_id": proposal_id,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "clarify"
    assert body["proposal"] is None
    still = client.get(f"/api/sodie/proposals/{proposal_id}")
    assert still.json()["status"] == "pending"


def test_preference_tweak_prompt_allowlists_fields():
    from app.services.sodie_llm import (
        PREFERENCE_TWEAK_SYSTEM,
        build_preference_tweak_prompt,
    )

    assert "propose_preference" in PREFERENCE_TWEAK_SYSTEM
    assert "max_cook_time_minutes" in PREFERENCE_TWEAK_SYSTEM
    assert "Never invent dietary" in PREFERENCE_TWEAK_SYSTEM
    messages = build_preference_tweak_prompt(
        {"max_cook_time_minutes": 60},
        "ACTIVE PAGE: analytics\ncompletion: 2/5",
        "Recipes take too long",
    )
    assert "CURRENT PREFERENCES" in messages[1].content
    assert "Recipes take too long" in messages[1].content


def test_preference_from_request_proposes_and_approve_applies(
    client, db: Session, test_user: User, monkeypatch
):
    from app.schemas.sodie_proposals import PreferenceTweakDraft

    test_user.max_cook_time_minutes = 60
    test_user.max_prep_time_minutes = 30
    db.commit()

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="propose_preference",
            max_cook_time_minutes=45,
            change_summary="Shorter cook time from too-hard feedback.",
            assistant_reply="Here’s a cook-time tweak for your review.",
        ),
    )

    created = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "request": "Recipes feel too hard — shorten my max cook time.",
            "idempotency_key": "pref-cook-1",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "proposal"
    proposal = body["proposal"]
    assert proposal["action_type"] == "propose_preference_tweak"
    assert proposal["status"] == "pending"
    assert proposal["diff"]["fields"]["max_cook_time_minutes"] == {
        "before": 60,
        "after": 45,
    }
    assert proposal["impact"]["plan_schedule"] == "unchanged until next generation"

    approved = client.post(f"/api/sodie/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "applied"

    db.refresh(test_user)
    assert test_user.max_cook_time_minutes == 45
    assert test_user.max_prep_time_minutes == 30


def test_preference_from_request_coach_qa_falls_through(
    client, test_user: User, monkeypatch
):
    from app.schemas.sodie_proposals import PreferenceTweakDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="coach_qa",
            change_summary="Analytics question.",
            assistant_reply="ok",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "request": "How is my streak looking?",
            "idempotency_key": "pref-coach-qa-1",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "coach_qa"
    assert body["proposal"] is None


def test_preference_from_request_needs_more_info(client, test_user: User, monkeypatch):
    from app.schemas.sodie_proposals import PreferenceTweakDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="needs_more_info",
            change_summary="Vague.",
            assistant_reply="Which preference — prep time, cook time, portions, or repeat?",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "request": "change my preferences somehow",
            "idempotency_key": "pref-vague-1",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "needs_more_info"
    assert body["proposal"] is None
    assert "prep" in body["assistant_message"].lower() or "preference" in body[
        "assistant_message"
    ].lower()


def test_preference_approve_expires_when_prefs_changed(
    client, db: Session, test_user: User, monkeypatch
):
    from app.schemas.sodie_proposals import PreferenceTweakDraft

    test_user.max_cook_time_minutes = 60
    db.commit()

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="propose_preference",
            max_cook_time_minutes=40,
            change_summary="Lower cook time.",
            assistant_reply="Here’s a tweak.",
        ),
    )
    created = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "request": "Shorten cook time",
            "idempotency_key": "pref-stale-1",
        },
    )
    assert created.status_code == 200
    proposal_id = created.json()["proposal"]["id"]

    test_user.max_cook_time_minutes = 55
    db.commit()

    approved = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert approved.status_code == 409
    stale = client.get(f"/api/sodie/proposals/{proposal_id}")
    assert stale.json()["status"] == "expired"


def test_preference_from_request_persists_on_analytics_thread(
    client, db: Session, test_user: User, monkeypatch
):
    from app.schemas.sodie_proposals import PreferenceTweakDraft

    test_user.preferred_portion_size = "2"
    db.commit()

    monkeypatch.setattr(
        "app.routers.sodie.generate_preference_tweak",
        lambda *_a, **_k: PreferenceTweakDraft(
            intent="propose_preference",
            preferred_portion_size="4",
            change_summary="Larger portions.",
            assistant_reply="Here’s a portion-size tweak.",
        ),
    )
    thread_id = client.post(
        "/api/sodie/threads",
        json={"scope": "analytics"},
    ).json()["id"]
    res = client.post(
        "/api/sodie/proposals/preferences/from-request",
        json={
            "request": "Bump my preferred portion size",
            "idempotency_key": "pref-thread-1",
            "thread_id": thread_id,
        },
    )
    assert res.status_code == 200
    msgs = client.get(f"/api/sodie/threads/{thread_id}").json()["messages"]
    assert [m["sender"] for m in msgs] == ["user", "ai"]
    assert "portion" in msgs[1]["content"].lower()
    listed = client.get(
        f"/api/sodie/threads/{thread_id}/proposals",
        params={"status": "pending"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["action_type"] == "propose_preference_tweak"


def test_recipe_pick_from_request_proposes_and_approve(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipePickDraft
    from uuid import UUID

    recipe = test_recipes[0]

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=UUID(str(recipe.id)),
            change_summary="Matches your cuisine streak.",
            assistant_reply="Here’s a recipe suggestion for your review.",
        ),
    )

    created = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "What should I cook next?",
            "idempotency_key": "pick-recipe-1",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["kind"] == "proposal"
    proposal = body["proposal"]
    assert proposal["action_type"] == "propose_recipe_pick"
    assert proposal["source_recipe_id"] == str(recipe.id)
    assert "adds to this week's plan" in proposal["impact"]["plan_schedule"]

    approved = client.post(f"/api/sodie/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "applied"
    assert approved.json()["source_recipe_id"] == str(recipe.id)


def test_recipe_pick_approve_schedules_onto_active_plan(
    client, db: Session, test_user: User, test_recipes: list, test_plan, monkeypatch
):
    from app.models import WeeklyPlanEntry
    from app.schemas.sodie_proposals import RecipePickDraft
    from app.services.weekly_plan import parse_recipe_schedule
    from uuid import UUID

    pick = test_recipes[1]
    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=UUID(str(pick.id)),
            change_summary="Good next cook.",
            assistant_reply="Try this one.",
        ),
    )
    created = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "Suggest a recipe for me",
            "idempotency_key": "pick-schedule-1",
        },
    )
    assert created.status_code == 200
    proposal_id = created.json()["proposal"]["id"]

    before = parse_recipe_schedule(test_plan.recipe_schedule)
    assert str(pick.id) not in before

    approved = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "applied"
    assert body["impact"]["schedule_outcome"] == "scheduled"
    assert body["impact"]["week_number"] == test_plan.week_number
    assert f"week {test_plan.week_number}" in body["impact"]["plan_schedule"]

    db.refresh(test_plan)
    after = parse_recipe_schedule(test_plan.recipe_schedule)
    assert str(pick.id) in after
    assert after[-1] == str(pick.id)
    entries = (
        db.query(WeeklyPlanEntry)
        .filter(WeeklyPlanEntry.weekly_plan_id == test_plan.id)
        .order_by(WeeklyPlanEntry.position.asc())
        .all()
    )
    assert any(str(e.catalog_recipe_id) == str(pick.id) for e in entries)


def test_recipe_pick_approve_already_on_plan_is_idempotent(
    client, db: Session, test_user: User, test_recipes: list, test_plan, monkeypatch
):
    from app.schemas.sodie_proposals import RecipePickDraft
    from app.services.weekly_plan import parse_recipe_schedule
    from uuid import UUID

    existing = test_recipes[0]
    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=UUID(str(existing.id)),
            change_summary="Already familiar.",
            assistant_reply="This one’s on your plan.",
        ),
    )
    created = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "Suggest something",
            "idempotency_key": "pick-already-1",
        },
    )
    proposal_id = created.json()["proposal"]["id"]
    before = parse_recipe_schedule(test_plan.recipe_schedule)

    approved = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["impact"]["schedule_outcome"] == "already_scheduled"
    db.refresh(test_plan)
    assert parse_recipe_schedule(test_plan.recipe_schedule) == before


def test_recipe_pick_approve_without_plan_still_applies(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipePickDraft
    from uuid import UUID

    pick = test_recipes[0]
    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=UUID(str(pick.id)),
            change_summary="No plan yet.",
            assistant_reply="Here’s a suggestion.",
        ),
    )
    created = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "What should I cook?",
            "idempotency_key": "pick-no-plan-1",
        },
    )
    proposal_id = created.json()["proposal"]["id"]
    approved = client.post(f"/api/sodie/proposals/{proposal_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "applied"
    assert approved.json()["impact"]["schedule_outcome"] == "no_active_plan"


def test_recipe_pick_rejects_invented_id(
    client, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipePickDraft
    import uuid

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="propose_recipe_pick",
            recipe_id=uuid.uuid4(),
            change_summary="Invented.",
            assistant_reply="Here’s one.",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "Suggest something spicy",
            "idempotency_key": "pick-invented-1",
        },
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "needs_more_info"
    assert res.json()["proposal"] is None


def test_recipe_pick_coach_qa_falls_through(
    client, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import RecipePickDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_pick",
        lambda *_a, **_k: RecipePickDraft(
            intent="coach_qa",
            change_summary="Streak question.",
            assistant_reply="ok",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/recipes/from-request",
        json={
            "request": "How is my streak?",
            "idempotency_key": "pick-coach-qa-1",
        },
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "coach_qa"


def test_recipe_pick_prompt_requires_candidates():
    from app.services.sodie_llm import RECIPE_PICK_SYSTEM, build_recipe_pick_prompt

    assert "propose_recipe_pick" in RECIPE_PICK_SYSTEM
    assert "Never invent" in RECIPE_PICK_SYSTEM
    messages = build_recipe_pick_prompt(
        [{"id": "abc", "name": "Soup"}],
        "ACTIVE PAGE: analytics",
        "What should I cook?",
    )
    assert "CANDIDATES" in messages[1].content
    assert "What should I cook?" in messages[1].content


def test_from_request_suggest_swap(client, test_recipes: list, monkeypatch):
    from app.schemas.sodie_proposals import RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="suggest_swap",
            change_summary="Different dish.",
            assistant_reply="Use Swap on the recipe card for a different dish.",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "this is too hard — give me something else",
            "idempotency_key": "enrich-swap-1",
        },
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "suggest_swap"
    assert res.json()["proposal"] is None
    assert "swap" in res.json()["assistant_message"].lower()


def test_from_request_out_of_scope(client, test_recipes: list, monkeypatch):
    from app.schemas.sodie_proposals import RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="out_of_scope",
            change_summary="Schedule ask.",
            assistant_reply="Move meals on Weekly Plan — I won’t patch the recipe for that.",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "move this dinner to Tuesday",
            "idempotency_key": "enrich-scope-1",
        },
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "out_of_scope"
    assert res.json()["proposal"] is None


def test_from_request_amount_ambiguous_becomes_needs_more_info(
    client, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            confidence="medium",
            amount_ambiguous=True,
            ingredients=[IngredientLine(name="oil", measure="some")],
            change_summary="Ambiguous oil amount.",
            assistant_reply="How much oil should I add?",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "add some oil",
            "idempotency_key": "enrich-ambig-1",
        },
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "needs_more_info"
    assert res.json()["proposal"] is None


def test_from_request_stores_allergen_safety_on_proposal(
    client, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    monkeypatch.setattr(
        "app.routers.sodie.generate_recipe_edit_patch",
        lambda *_a, **_k: RecipeEditPatchDraft(
            intent="propose_edit",
            confidence="high",
            allergen_conflict=True,
            safety_notes="Adds peanuts; you listed peanuts as an allergen.",
            ingredients=[
                IngredientLine(name="flour", measure="2 cups"),
                IngredientLine(name="peanuts", measure="1/2 cup"),
            ],
            change_summary="Added peanuts.",
            assistant_reply="Here’s a proposal — note the allergen warning.",
        ),
    )
    res = client.post(
        "/api/sodie/proposals/from-request",
        json={
            "source_recipe_id": str(test_recipes[0].id),
            "request": "add peanuts",
            "idempotency_key": "enrich-allergen-1",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "proposal"
    impact = body["proposal"]["impact"]
    assert impact["allergen_conflict"] is True
    assert "peanut" in (impact.get("safety_notes") or "").lower()
    assert impact["plan_schedule"] == "unchanged until weekly_plan_entries"
    assert impact["shopping_list"] == "unchanged until shopping reconciliation"
