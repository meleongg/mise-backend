"""Regression coverage for pantry/Sodie RLS migration and app-layer isolation."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.utils.auth import get_current_user
from tests.conftest import app, override_get_current_user
import app.utils.password as password_utils

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c7d8e9f0_enable_rls_pantry_and_sodie.py"
)
_spec = spec_from_file_location(
    "c7d8e9f0_enable_rls_pantry_and_sodie", _MIGRATION_PATH
)
assert _spec is not None and _spec.loader is not None
rls_migration = module_from_spec(_spec)
_spec.loader.exec_module(rls_migration)


def _hash(password: str) -> str:
    return password_utils.hash_password(password)


def _make_user(*, email: str, first_name: str, last_name: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        first_name=first_name,
        last_name=last_name,
        cuisine="Italian",
        frequency=3,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password=_hash("OtherUser123!"),
    )


def _as_user(user_id: uuid.UUID):
    def override(db_session: Session = Depends(get_db)) -> User:
        return db_session.query(User).filter(User.id == user_id).one()

    return override


def test_rls_migration_revises_sodie_head_and_targets_expected_tables():
    assert rls_migration.down_revision == "f2b8c1d4"
    assert rls_migration.revision == "c7d8e9f0"
    assert rls_migration._RLS_TABLES == (
        "user_pantry_items",
        "sodie_threads",
        "sodie_messages",
    )


def test_rls_migration_upgrade_downgrade_sql_is_restrictive(monkeypatch):
    """ENABLE without CREATE POLICY keeps Data API deny-by-default for non-bypass roles."""
    statements = []

    def capture(sql):
        statements.append(" ".join(str(sql).split()))

    monkeypatch.setattr(rls_migration.op, "execute", capture)

    rls_migration.upgrade()
    assert statements == [
        "ALTER TABLE user_pantry_items ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE sodie_threads ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE sodie_messages ENABLE ROW LEVEL SECURITY",
    ]
    assert all("CREATE POLICY" not in sql for sql in statements)
    assert all("FORCE ROW LEVEL SECURITY" not in sql for sql in statements)

    statements.clear()
    rls_migration.downgrade()
    assert statements == [
        "ALTER TABLE sodie_messages DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE sodie_threads DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE user_pantry_items DISABLE ROW LEVEL SECURITY",
    ]


def test_rls_migration_documents_role_audit_and_rollback():
    source = _MIGRATION_PATH.read_text()
    assert "BYPASSRLS" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "Rollback risk" in source
    assert "weekly_plans" in source
    assert "user_recipe_progress" in source


def test_pantry_items_are_isolated_between_users(client, db: Session, test_user: User):
    other = _make_user(
        email="other-pantry@example.com", first_name="Other", last_name="User"
    )
    db.add(other)
    db.flush()

    seeded = client.put(
        "/api/users/pantry",
        json={"items": [{"name": "Olive oil", "is_baseline": True}]},
    )
    assert seeded.status_code == 200
    assert len(seeded.json()) == 1

    app.dependency_overrides[get_current_user] = _as_user(other.id)
    try:
        response = client.get("/api/users/pantry")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user


def test_sodie_thread_access_is_denied_across_users(client, db: Session, test_user: User):
    created = client.post("/api/sodie/threads", json={"scope": "global"})
    assert created.status_code == 200
    thread_id = created.json()["id"]

    other = _make_user(
        email="other-sodie@example.com", first_name="Other", last_name="Sodie"
    )
    db.add(other)
    db.flush()

    app.dependency_overrides[get_current_user] = _as_user(other.id)
    try:
        assert client.get(f"/api/sodie/threads/{thread_id}").status_code == 404
        assert (
            client.post(
                f"/api/sodie/threads/{thread_id}/messages",
                json={"content": "peek"},
            ).status_code
            == 404
        )
        assert client.delete(f"/api/sodie/threads/{thread_id}").status_code == 404
        history = client.get("/api/sodie/threads")
        assert history.status_code == 200
        assert history.json() == []
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user
