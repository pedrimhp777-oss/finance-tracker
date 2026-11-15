"""Testes de token inválido/expirado, /auth/me e rate limiting."""
import pytest

from app import security
from test_transactions import get_auth_headers


# --- /auth/me ---

def test_get_me_success(client):
    headers = get_auth_headers(client, "me@email.com")
    res = client.get("/auth/me", headers=headers)
    assert res.status_code == 200
    assert res.json()["email"] == "me@email.com"


def test_get_me_invalid_token(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer token-invalido"})
    assert res.status_code == 401


def test_get_me_expired_token(client):
    from jose import jwt

    client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "exp@email.com", "password": "password123"},
    )
    token = jwt.encode(
        {"sub": "1", "exp": 0},  # expirado em 1970
        "test-only-secret-key-never-use-in-production",
        algorithm="HS256",
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_get_me_missing_sub(client):
    from jose import jwt

    token = jwt.encode(
        {"exp": 9999999999},
        "test-only-secret-key-never-use-in-production",
        algorithm="HS256",
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# --- Rate limiting ---

def test_login_rate_limit_after_repeated_failures(client):
    client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "ratelimit@email.com", "password": "password123"},
    )
    email = "ratelimit@email.com"
    status_codes = []
    for _ in range(15):
        res = client.post(
            "/auth/login", data={"username": email, "password": "errada"}
        )
        status_codes.append(res.status_code)
        if res.status_code == 429:
            break

    # Depois de muitas tentativas, o limite deve ser acionado.
    assert 429 in status_codes
