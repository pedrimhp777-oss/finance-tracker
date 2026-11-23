"""Testes de revogação de sessão via `users.token_version`.

Cobre: logout invalida tokens anteriores, tokens novos funcionam, outros
usuários não são afetados e tokens legados (sem `tv`) seguem válidos até o
primeiro logout.
"""
import jwt

from app.security import ALGORITHM, SECRET_KEY
from test_transactions import get_auth_headers


def _login(client, email, password="password123"):
    res = client.post("/auth/login", data={"username": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _register(client, email, password="password123"):
    return client.post(
        "/auth/register",
        json={"name": "Rev", "email": email, "password": password},
    )


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- Comportamento principal ---

def test_logout_revokes_previous_token(client):
    _register(client, "logout@email.com")
    token = _login(client, "logout@email.com")

    assert client.get("/auth/me", headers=_auth(token)).status_code == 200

    out = client.post("/auth/logout", headers=_auth(token))
    assert out.status_code == 204

    # O mesmo token não vale mais, mesmo dentro da validade do `exp`.
    assert client.get("/auth/me", headers=_auth(token)).status_code == 401
    assert client.get("/transactions/", headers=_auth(token)).status_code == 401


def test_login_after_logout_works(client):
    _register(client, "relogin@email.com")
    first = _login(client, "relogin@email.com")
    client.post("/auth/logout", headers=_auth(first))

    second = _login(client, "relogin@email.com")
    assert second != first
    assert client.get("/auth/me", headers=_auth(second)).status_code == 200
    # O token antigo continua inválido.
    assert client.get("/auth/me", headers=_auth(first)).status_code == 401


def test_token_version_increments_in_token(client):
    _register(client, "tvcount@email.com")
    first = _login(client, "tvcount@email.com")
    assert jwt.decode(first, SECRET_KEY, algorithms=[ALGORITHM])["tv"] == 0

    client.post("/auth/logout", headers=_auth(first))
    second = _login(client, "tvcount@email.com")
    assert jwt.decode(second, SECRET_KEY, algorithms=[ALGORITHM])["tv"] == 1

    client.post("/auth/logout", headers=_auth(second))
    third = _login(client, "tvcount@email.com")
    assert jwt.decode(third, SECRET_KEY, algorithms=[ALGORITHM])["tv"] == 2


def test_logout_does_not_affect_other_users(client):
    _register(client, "keep@email.com")
    _register(client, "other@email.com")
    keep = _login(client, "keep@email.com")
    other = _login(client, "other@email.com")

    client.post("/auth/logout", headers=_auth(other))

    assert client.get("/auth/me", headers=_auth(keep)).status_code == 200
    assert client.get("/auth/me", headers=_auth(other)).status_code == 401


def test_multiple_sessions_revoked_together(client):
    """Logout revoga TODAS as sessões do usuário, não só a atual.

    Obs.: dois logins no mesmo segundo produzem tokens idênticos (mesmas
    claims + HS256 determinístico), então o teste não assume que `a != b` —
    isso é comportamento esperado do JWT, não um bug.
    """
    _register(client, "multi@email.com")
    a = _login(client, "multi@email.com")
    b = _login(client, "multi@email.com")

    # Sessões paralelas antes do logout continuam válidas.
    assert client.get("/auth/me", headers=_auth(a)).status_code == 200
    assert client.get("/auth/me", headers=_auth(b)).status_code == 200

    client.post("/auth/logout", headers=_auth(a))

    # Após o logout, NENHUM token anterior vale — inclusive o da outra sessão.
    assert client.get("/auth/me", headers=_auth(a)).status_code == 401
    assert client.get("/auth/me", headers=_auth(b)).status_code == 401


# --- Compatibilidade e casos de borda ---

def test_legacy_token_without_tv_still_valid_before_logout(client):
    """Tokens emitidos antes desta feature (sem `tv`) continuam válidos."""
    _register(client, "legacy-tv@email.com")
    token = _login(client, "legacy-tv@email.com")
    me = client.get("/auth/me", headers=_auth(token)).json()

    legacy = jwt.encode(
        {"sub": str(me["id"]), "exp": 9999999999},  # sem "tv"
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert client.get("/auth/me", headers=_auth(legacy)).status_code == 200

    # Mas deixa de valer assim que o usuário faz logout.
    client.post("/auth/logout", headers=_auth(token))
    assert client.get("/auth/me", headers=_auth(legacy)).status_code == 401


def test_tampered_tv_value_is_rejected(client):
    """`tv` forjado (maior ou não-inteiro) não abre sessão."""
    _register(client, "tamper-tv@email.com")
    token = _login(client, "tamper-tv@email.com")
    me = client.get("/auth/me", headers=_auth(token)).json()

    ahead = jwt.encode(
        {"sub": str(me["id"]), "exp": 9999999999, "tv": 99},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert client.get("/auth/me", headers=_auth(ahead)).status_code == 401

    wrong_type = jwt.encode(
        {"sub": str(me["id"]), "exp": 9999999999, "tv": "1"},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert client.get("/auth/me", headers=_auth(wrong_type)).status_code == 401


def test_logout_requires_authentication(client):
    assert client.post("/auth/logout").status_code == 401
    assert client.post("/auth/logout", headers=_auth("invalido")).status_code == 401


# --- Escopo de cache continua correto ---

def test_logout_has_no_store(client):
    _register(client, "logout-cache@email.com")
    token = _login(client, "logout-cache@email.com")
    res = client.post("/auth/logout", headers=_auth(token))
    assert res.status_code == 204
    assert res.headers["cache-control"] == "no-store"


# --- Isolamento de dados continua intacto após revogação ---

def test_isolation_preserved_after_logout(client):
    a = get_auth_headers(client, "iso-rev-a@email.com")
    b = get_auth_headers(client, "iso-rev-b@email.com")
    created = client.post(
        "/transactions/",
        json={"description": "A", "amount": "10", "type": "expense", "category": "Outros"},
        headers=a,
    ).json()

    # B não acessa dados de A mesmo com sessão válida.
    assert client.get(f"/transactions/{created['id']}", headers=b).status_code == 404

    # Logout de B não afeta A.
    client.post("/auth/logout", headers=b)
    assert client.get(f"/transactions/{created['id']}", headers=a).status_code == 200
