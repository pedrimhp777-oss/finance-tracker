"""Testes do modo de autenticação por cookie HttpOnly + CSRF.

Cobrem: emissão dos cookies no login, autenticação por cookie, bloqueio CSRF
nos métodos que alteram estado, precedência do header Bearer, limpeza no
logout e atributos de segurança dos cookies.
"""
import jwt
import pytest

from app import config
from app.security import ALGORITHM, SECRET_KEY
from test_transactions import get_auth_headers

AUTH = config.AUTH_COOKIE_NAME
CSRF = config.CSRF_COOKIE_NAME
CSRF_HEADER = config.CSRF_HEADER_NAME
SAFE_ATTRS = {"httponly", "secure", "samesite"}


def _register(client, email, password="password123"):
    return client.post(
        "/auth/register", json={"name": "Cookie", "email": email, "password": password}
    )


def _login(client, email, password="password123"):
    return client.post("/auth/login", data={"username": email, "password": password})


def _set_cookie_headers(res) -> dict[str, str]:
    """Mapeia nome do cookie -> header Set-Cookie bruto."""
    raw = res.headers.get_list("set-cookie") if hasattr(res.headers, "get_list") else []
    if not raw:
        raw = [v for k, v in res.headers.items() if k.lower() == "set-cookie"]
    out = {}
    for item in raw:
        name = item.split("=", 1)[0].strip()
        out[name] = item
    return out


# --- Emissão dos cookies ---

def test_login_sets_httponly_session_cookie(client):
    _register(client, "cookie-set@email.com")
    res = _login(client, "cookie-set@email.com")
    assert res.status_code == 200

    cookies = _set_cookie_headers(res)
    assert AUTH in cookies, cookies
    session_cookie = cookies[AUTH].lower()
    assert "httponly" in session_cookie
    assert "path=/" in session_cookie
    # Em ambiente de teste (dev) o cookie não leva Secure, mas leva SameSite.
    assert "samesite=lax" in session_cookie


def test_login_sets_readable_csrf_cookie(client):
    _register(client, "cookie-csrf@email.com")
    res = _login(client, "cookie-csrf@email.com")

    cookies = _set_cookie_headers(res)
    assert CSRF in cookies, cookies
    # O token CSRF precisa ser legível por JS (sem HttpOnly).
    assert "httponly" not in cookies[CSRF].lower()


def test_login_still_returns_token_in_body(client):
    """Transição suave: clientes que usam Bearer continuam atendidos."""
    _register(client, "cookie-body@email.com")
    res = _login(client, "cookie-body@email.com")
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    payload = jwt.decode(body["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert "tv" in payload


# --- Autenticação por cookie ---

def test_cookie_authenticates_get_requests(client):
    _register(client, "cookie-auth@email.com")
    _login(client, "cookie-auth@email.com")

    # Sem header Authorization: o TestClient mantém o cookie do login.
    res = client.get("/auth/me", headers={"Authorization": ""})
    assert res.status_code == 200
    assert res.json()["email"] == "cookie-auth@email.com"

    assert client.get("/transactions/", headers={"Authorization": ""}).status_code == 200


def test_no_credentials_at_all_is_401(client):
    assert client.get("/auth/me").status_code == 401


# --- CSRF: métodos que alteram estado ---

def test_cookie_post_without_csrf_header_is_blocked(client):
    _register(client, "csrf-block@email.com")
    _login(client, "csrf-block@email.com")

    res = client.post(
        "/transactions/",
        json={"description": "X", "amount": "10", "type": "expense", "category": "Outros"},
        headers={"Authorization": ""},
    )
    assert res.status_code == 403
    assert "CSRF" in res.json()["detail"]


def test_cookie_post_with_valid_csrf_header_works(client):
    _register(client, "csrf-ok@email.com")
    _login(client, "csrf-ok@email.com")

    csrf_token = client.cookies.get(CSRF)
    assert csrf_token

    res = client.post(
        "/transactions/",
        json={"description": "Com CSRF", "amount": "10", "type": "expense", "category": "Outros"},
        headers={"Authorization": "", CSRF_HEADER: csrf_token},
    )
    assert res.status_code == 201
    assert res.json()["description"] == "Com CSRF"


def test_cookie_post_with_wrong_csrf_header_is_blocked(client):
    _register(client, "csrf-wrong@email.com")
    _login(client, "csrf-wrong@email.com")

    res = client.post(
        "/transactions/",
        json={"description": "X", "amount": "10", "type": "expense", "category": "Outros"},
        headers={"Authorization": "", CSRF_HEADER: "valor-errado"},
    )
    assert res.status_code == 403


def test_cookie_delete_without_csrf_is_blocked(client):
    headers = get_auth_headers(client, "csrf-del@email.com")
    tx = client.post(
        "/transactions/",
        json={"description": "alvo", "amount": "5", "type": "expense", "category": "Outros"},
        headers=headers,
    ).json()

    _login(client, "csrf-del@email.com")
    res = client.delete(f"/transactions/{tx['id']}", headers={"Authorization": ""})
    assert res.status_code == 403


def test_safe_methods_do_not_require_csrf(client):
    _register(client, "csrf-safe@email.com")
    _login(client, "csrf-safe@email.com")
    # GET, HEAD e OPTIONS não exigem token CSRF.
    assert client.get("/transactions/", headers={"Authorization": ""}).status_code == 200
    assert client.get("/transactions/summary", headers={"Authorization": ""}).status_code == 200
    assert client.get("/transactions/categories", headers={"Authorization": ""}).status_code == 200


# --- Precedência do Bearer ---

def test_bearer_takes_precedence_and_skips_csrf(client):
    """Com header Bearer não há CSRF: um site terceiro não define esse header."""
    headers = get_auth_headers(client, "bearer-wins@email.com")
    res = client.post(
        "/transactions/",
        json={"description": "Bearer", "amount": "7", "type": "expense", "category": "Outros"},
        headers=headers,
    )
    assert res.status_code == 201


def test_invalid_bearer_falls_back_to_error_not_cookie(client):
    """Bearer inválido não deve silenciosamente usar o cookie."""
    _register(client, "bearer-bad@email.com")
    _login(client, "bearer-bad@email.com")
    res = client.get("/auth/me", headers={"Authorization": "Bearer invalido"})
    assert res.status_code == 401


# --- Logout limpa os cookies ---

def test_logout_clears_cookies(client):
    _register(client, "cookie-logout@email.com")
    _login(client, "cookie-logout@email.com")
    csrf_token = client.cookies.get(CSRF)

    res = client.post(
        "/auth/logout", headers={"Authorization": "", CSRF_HEADER: csrf_token}
    )
    assert res.status_code == 204

    cleared = _set_cookie_headers(res)
    assert cleared, "logout deve emitir Set-Cookie de remoção"
    assert client.get("/auth/me", headers={"Authorization": ""}).status_code == 401


# --- Configuração de cookies por ambiente ---

def _config_flags_under_env(app_env: str) -> str:
    """Lê os flags de cookie com um APP_ENV específico (config é import-time)."""
    import os
    import pathlib
    import subprocess
    import sys

    repo_root = str(pathlib.Path(__file__).parent.resolve())
    env = dict(os.environ)
    env.update({"APP_ENV": app_env, "PYTHONPATH": repo_root})
    script = (
        "from app import config\n"
        "print('SECURE=' + str(config.COOKIE_SECURE))\n"
        "print('SAMESITE=' + config.COOKIE_SAMESITE)\n"
        "print('DEV=' + str(config.IS_DEV_ENV))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=repo_root, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr[-500:]
    return out.stdout


def test_cookie_flags_in_production():
    """Produção: Secure obrigatório + SameSite=None (cross-site Vercel->Render)."""
    out = _config_flags_under_env("production")
    assert "SECURE=True" in out
    assert "SAMESITE=none" in out
    assert "DEV=False" in out


@pytest.mark.parametrize("env", ["development", "dev", "local", "test"])
def test_cookie_flags_in_dev_allow_http(env):
    """Dev local roda em HTTP: cookie sem Secure e SameSite=Lax."""
    out = _config_flags_under_env(env)
    assert "SECURE=False" in out
    assert "SAMESITE=lax" in out
    assert "DEV=True" in out
