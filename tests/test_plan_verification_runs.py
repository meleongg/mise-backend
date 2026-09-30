"""Tests for recipe_verification_runs persistence and client-safe detail."""

from __future__ import annotations

import json
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import Recipe, RecipeVerificationRun, User
from app.services.plan_evaluator import EVALUATOR_KIND, evaluate_plan_candidates
from app.services.plan_verification import verify_plan_candidates
from app.services.plan_verification_runs import (
    client_summary_for_failed_run,
    latest_verification_run_for_user,
    record_plan_verification_run,
)


def test_migration_revises_serving_scale_head():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "h6c7d8e9_recipe_verification_runs.py"
    )
    spec = spec_from_file_location("h6c7d8e9", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "h6c7d8e9"
    assert mod.down_revision == "g5b6c7d8"
    assert "ENABLE ROW LEVEL SECURITY" in path.read_text()


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"runs-{uuid.uuid4()}@example.com",
        first_name="Gate",
        last_name="Tester",
        cuisine="Italian",
        frequency=1,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password="x",
        dietary_restrictions=json.dumps([]),
        allergens=json.dumps(["dairy"]),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _recipe(**overrides) -> Recipe:
    recipe = Recipe(
        id=uuid.uuid4(),
        external_id=f"ext-{uuid.uuid4()}",
        name="Creamy Pasta",
        cuisine="Italian",
        ingredients=json.dumps([{"name": "pasta", "measure": "200g"}]),
        instructions="Boil.",
        difficulty="medium",
        dietary_tags=json.dumps(["vegetarian"]),
        allergens=json.dumps(["dairy", "gluten"]),
        prep_time_minutes=15,
        cook_time_minutes=20,
    )
    for key, value in overrides.items():
        setattr(recipe, key, value)
    return recipe


def test_as_client_detail_strips_recipe_ids():
    recipe = _recipe()
    user = _user(frequency=1)
    gate = verify_plan_candidates(user, [recipe.id], [recipe])
    assert gate.ok is False
    client = gate.as_client_detail(verification_run_id="abc")
    assert "failures" not in client
    assert "recipe_id" not in json.dumps(client)
    assert "Creamy Pasta" not in json.dumps(client)
    assert "allergen_conflict" in client["failure_codes"]
    assert client["verification_run_id"] == "abc"


def test_record_failed_and_passed_runs(db, test_user):
    bad = _recipe()
    db.add(bad)
    db.flush()
    test_user.allergens = json.dumps(["dairy"])
    test_user.frequency = 1
    db.flush()

    fail_gate = verify_plan_candidates(test_user, [bad.id], [bad])
    assert fail_gate.ok is False
    failed = record_plan_verification_run(
        db,
        user=test_user,
        target_week_number=1,
        flow="initial",
        gate=fail_gate,
        candidate_ids=[bad.id],
        search_attempts=2,
        generation_attempts=1,
    )
    db.flush()
    assert failed.final_status == "failed"
    assert failed.evaluator_kind == "deterministic_stub_v1"
    assert failed.deterministic_passed is False
    codes = json.loads(failed.failure_codes_json)
    assert "allergen_conflict" in codes
    detail = json.loads(failed.failures_detail_json)
    assert any(row.get("recipe_id") == str(bad.id) for row in detail)

    safe = _recipe(
        allergens=json.dumps([]),
        name="Safe Salad",
        external_id=f"safe-{uuid.uuid4()}",
    )
    db.add(safe)
    db.flush()
    pass_gate = verify_plan_candidates(test_user, [safe.id], [safe])
    assert pass_gate.ok is True
    evaluation = evaluate_plan_candidates(db, test_user, [safe])
    passed = record_plan_verification_run(
        db,
        user=test_user,
        target_week_number=1,
        flow="regenerate",
        gate=pass_gate,
        candidate_ids=[safe.id],
        evaluation=evaluation,
        attempt_number=2,
        auto_repaired=True,
    )
    db.flush()
    assert passed.final_status == "passed"
    assert passed.evaluator_kind == EVALUATOR_KIND
    assert passed.attempt_number == 2
    output = json.loads(passed.evaluator_output_json)
    assert output["auto_repaired"] is True
    assert "confidence" in output

    latest = latest_verification_run_for_user(db, test_user.id)
    assert latest is not None
    assert latest.id == passed.id
    assert client_summary_for_failed_run(latest) is None

    # Latest failed surfaces codes only
    failed_again = record_plan_verification_run(
        db,
        user=test_user,
        target_week_number=1,
        flow="regenerate",
        gate=fail_gate,
        candidate_ids=[bad.id],
    )
    db.flush()
    summary = client_summary_for_failed_run(
        latest_verification_run_for_user(db, test_user.id)
    )
    assert summary is not None
    assert summary["final_status"] == "failed"
    assert "allergen_conflict" in summary["failure_codes"]
    assert "recipe_id" not in json.dumps(summary)
    assert summary["verification_run_id"] == str(failed_again.id)


def test_eligibility_includes_last_failed_verification(client, db, test_user):
    bad = _recipe()
    db.add(bad)
    db.flush()
    test_user.allergens = json.dumps(["dairy"])
    test_user.frequency = 1
    db.flush()
    gate = verify_plan_candidates(test_user, [bad.id], [bad])
    record_plan_verification_run(
        db,
        user=test_user,
        target_week_number=1,
        flow="initial",
        gate=gate,
        candidate_ids=[bad.id],
        attempt_number=2,
    )
    db.flush()

    response = client.get(f"/plan/can_generate_next_week/{test_user.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["last_generation_verification"] is not None
    assert "allergen_conflict" in body["last_generation_verification"]["failure_codes"]
    assert body["last_generation_verification"]["attempt_number"] == 2
    assert body["last_generation_verification"]["auto_repair_exhausted"] is True
    assert "Creamy Pasta" not in json.dumps(body)
