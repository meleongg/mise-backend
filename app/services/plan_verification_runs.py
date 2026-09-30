"""Persist recipe_verification_runs for weekly plan generate attempts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import RecipeVerificationRun, User
from app.services.plan_verification import PlanVerificationResult


EVALUATOR_KIND_STUB = "deterministic_stub_v1"


def record_plan_verification_run(
    db: Session,
    *,
    user: User,
    target_week_number: int,
    flow: str,
    gate: PlanVerificationResult,
    candidate_ids: Sequence[uuid.UUID],
    search_attempts: Optional[int] = None,
    generation_attempts: Optional[int] = None,
    attempt_number: int = 1,
) -> RecipeVerificationRun:
    """Insert one audit row for a gate outcome (pass or fail)."""
    codes = gate.failure_codes()
    display = None if gate.ok else gate.as_detail()["message"]
    stub_output = {
        "source": "verify_plan_candidates",
        "failure_count": len(gate.failures),
        "failure_codes": codes,
    }
    run = RecipeVerificationRun(
        id=uuid.uuid4(),
        user_id=user.id,
        target_week_number=int(target_week_number),
        flow=flow,
        attempt_number=int(attempt_number),
        final_status="passed" if gate.ok else "failed",
        deterministic_passed=bool(gate.ok),
        failure_codes_json=json.dumps(codes),
        failures_detail_json=json.dumps([f.as_dict() for f in gate.failures]),
        candidate_count=len(candidate_ids),
        candidate_recipe_ids_json=json.dumps([str(rid) for rid in candidate_ids]),
        search_attempts=search_attempts,
        generation_attempts=generation_attempts,
        evaluator_kind=EVALUATOR_KIND_STUB,
        evaluator_passed=bool(gate.ok),
        evaluator_output_json=json.dumps(stub_output),
        evaluator_model_id=None,
        display_message=display,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def latest_verification_run_for_user(
    db: Session, user_id: uuid.UUID
) -> Optional[RecipeVerificationRun]:
    return (
        db.query(RecipeVerificationRun)
        .filter(RecipeVerificationRun.user_id == user_id)
        .order_by(RecipeVerificationRun.created_at.desc())
        .first()
    )


def client_summary_for_failed_run(
    run: Optional[RecipeVerificationRun],
) -> Optional[dict[str, Any]]:
    """Safe eligibility payload; None when missing or latest run passed."""
    if run is None or run.final_status != "failed":
        return None
    try:
        codes = json.loads(run.failure_codes_json or "[]")
    except json.JSONDecodeError:
        codes = []
    if not isinstance(codes, list):
        codes = []
    return {
        "final_status": "failed",
        "failure_codes": [str(c) for c in codes],
        "target_week_number": run.target_week_number,
        "failed_at": run.created_at.isoformat() if run.created_at else None,
        "verification_run_id": str(run.id),
    }
