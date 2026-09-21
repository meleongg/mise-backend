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
    assert created.json()["proposal"]["impact"]["shopping_list"] == "deferred"
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
    messages = build_recipe_edit_patch_prompt(
        {
            "title": "Cookies",
            "ingredients": [{"name": "salt", "measure": "1/4 tsp"}],
            "notes": None,
        },
        "can we make the cookies saltier",
    )
    assert "CURRENT RECIPE" in messages[1].content
    assert "can we make the cookies saltier" in messages[1].content
    assert "PENDING PROPOSAL: none" in messages[1].content


def test_create_proposal_from_request_uses_structured_patch(
    client, db: Session, test_user: User, test_recipes: list, monkeypatch
):
    from app.schemas.sodie_proposals import IngredientLine, RecipeEditPatchDraft

    recipe = test_recipes[0]

    def fake_generate(snapshot, request, pending_diff=None):
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
