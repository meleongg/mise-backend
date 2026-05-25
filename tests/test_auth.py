def test_signup(client):
    """Test user signup with a policy-compliant password."""
    response = client.post(
        "/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "NewUser123!",
            "first_name": "John",
            "last_name": "Doe",
            "cuisine": "Mexican",
            "frequency": 4,
            "skill_level": "beginner",
            "user_goal": "Eat Healthier",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert "access_token" in data
    assert data["user"]["email"] == "newuser@example.com"


def test_signup_rejects_weak_password(client):
    """Backend must reject passwords that don't meet the policy."""
    response = client.post(
        "/auth/register",
        json={
            "email": "weakpw@example.com",
            "password": "short",
            "first_name": "Weak",
            "last_name": "Password",
        },
    )
    assert response.status_code == 422


def test_signup_rejects_letters_only(client):
    """Policy requires at least one digit."""
    response = client.post(
        "/auth/register",
        json={
            "email": "lettersonly@example.com",
            "password": "letters-only",
            "first_name": "Letters",
            "last_name": "Only",
        },
    )
    assert response.status_code == 422


def test_signup_accepts_complex_password(client):
    """Special characters and long passphrases must be allowed."""
    response = client.post(
        "/auth/register",
        json={
            "email": "complex@example.com",
            "password": "C0rrect horse battery staple! €",
            "first_name": "Complex",
            "last_name": "Pass",
        },
    )
    assert response.status_code == 201


def test_login(client, test_user):
    """Test user login"""
    response = client.post(
        "/auth/login",
        json={"email": "testuser@example.com", "password": "test123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user"]["email"] == "testuser@example.com"


def test_login_normalizes_email_case(client, test_user):
    """Login should accept mixed-case email since both sides normalize."""
    response = client.post(
        "/auth/login",
        json={"email": "TESTUSER@Example.com", "password": "test123"},
    )
    assert response.status_code == 200


def test_login_invalid_password(client, test_user):
    """Test login with wrong password"""
    response = client.post(
        "/auth/login",
        json={"email": "testuser@example.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401
