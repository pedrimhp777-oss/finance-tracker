"""Testes de `Cache-Control: no-store` nas respostas financeiras.

A proteção é centralizada no `NoStoreMiddleware` (app/middleware.py) e
aplicada por prefixo de caminho, cobrindo todos os endpoints de
transações — incluindo respostas de erro — sem lógica por endpoint.
"""
import io

import openpyxl
import pytest

from test_transactions import get_auth_headers

NO_STORE = "no-store"


def _create(client, headers, **overrides):
    payload = {
        "description": "Item",
        "amount": "10.50",
        "type": "expense",
        "category": "Alimentação",
    }
    payload.update(overrides)
    return client.post("/transactions/", json=payload, headers=headers)


# --- 200 / 2xx: dados financeiros ---

def test_list_has_no_store(client):
    headers = get_auth_headers(client, "nostore-list@email.com")
    res = client.get("/transactions/", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE


def test_summary_has_no_store(client):
    headers = get_auth_headers(client, "nostore-sum@email.com")
    res = client.get("/transactions/summary", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE


def test_categories_has_no_store(client):
    headers = get_auth_headers(client, "nostore-cat@email.com")
    res = client.get("/transactions/categories", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE


def test_get_single_transaction_has_no_store(client):
    headers = get_auth_headers(client, "nostore-get@email.com")
    tx_id = _create(client, headers).json()["id"]
    res = client.get(f"/transactions/{tx_id}", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE


def test_create_transaction_has_no_store(client):
    headers = get_auth_headers(client, "nostore-post@email.com")
    res = _create(client, headers)
    assert res.status_code == 201
    assert res.headers["cache-control"] == NO_STORE


def test_update_transaction_has_no_store(client):
    headers = get_auth_headers(client, "nostore-put@email.com")
    tx_id = _create(client, headers).json()["id"]
    res = client.put(
        f"/transactions/{tx_id}",
        json={
            "description": "Editada",
            "amount": "20.00",
            "type": "income",
            "category": "Trabalho",
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE


def test_delete_transaction_has_no_store(client):
    headers = get_auth_headers(client, "nostore-del@email.com")
    tx_id = _create(client, headers).json()["id"]
    res = client.delete(f"/transactions/{tx_id}", headers=headers)
    assert res.status_code == 204
    assert res.headers["cache-control"] == NO_STORE


# --- EXPORTAÇÃO EXCEL: header presente e download intacto ---

def test_excel_export_has_no_store_and_valid_workbook(client):
    headers = get_auth_headers(client, "nostore-xlsx@email.com")
    _create(client, headers, description="Mercado", amount="99.90")

    res = client.get("/transactions/export/excel", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE
    # O download não pode ser prejudicado pelo middleware.
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in res.headers["content-disposition"]
    assert "relatorio_financeiro.xlsx" in res.headers["content-disposition"]
    # Corpo continua sendo um xlsx válido (StreamingResponse não foi consumido).
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    ws = wb.active
    assert ws["A1"].value == "Descrição"
    assert ws.max_row >= 2


# --- RESPOSTAS DE ERRO: a proteção também precisa valer ---

def test_unauthenticated_error_has_no_store(client):
    res = client.get("/transactions/")
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


def test_not_found_error_has_no_store(client):
    headers = get_auth_headers(client, "nostore-404@email.com")
    res = client.get("/transactions/999999", headers=headers)
    assert res.status_code == 404
    assert res.headers["cache-control"] == NO_STORE


def test_validation_error_has_no_store(client):
    headers = get_auth_headers(client, "nostore-422@email.com")
    res = client.get("/transactions/", params={"month": 13}, headers=headers)
    assert res.status_code == 422
    assert res.headers["cache-control"] == NO_STORE


def test_forbidden_cross_user_error_has_no_store(client):
    owner = get_auth_headers(client, "nostore-owner@email.com")
    tx_id = _create(client, owner).json()["id"]
    intruder = get_auth_headers(client, "nostore-intruder@email.com")
    res = client.delete(f"/transactions/{tx_id}", headers=intruder)
    assert res.status_code == 404
    assert res.headers["cache-control"] == NO_STORE


# --- ESCOPO: não vazar para rotas fora de /transactions ---

def test_health_does_not_get_no_store(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers.get("cache-control") != NO_STORE


def test_root_does_not_get_no_store(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("cache-control") != NO_STORE


# --- CORS não é prejudicado ---

def test_cors_preflight_is_unaffected(client):
    res = client.options(
        "/transactions/",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    # Preflight é excluído de propósito (cache via Access-Control-Max-Age).
    assert res.headers.get("cache-control") != NO_STORE


def test_cors_headers_preserved_on_read(client):
    headers = get_auth_headers(client, "nostore-cors@email.com")
    res = client.get(
        "/transactions/", headers={**headers, "Origin": "http://localhost:5173"}
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert res.headers.get("access-control-allow-credentials") == "true"


# --- Unidade: o middleware age por prefixo, não por endpoint ---

@pytest.mark.parametrize(
    "path",
    [
        "/transactions",
        "/transactions/",
        "/transactions/42",
        "/transactions/summary",
        "/transactions/categories",
        "/transactions/export/excel",
        "/auth/login",
        "/auth/register",
        "/auth/me",
    ],
)
def test_sensitive_path_matches_all_protected_routes(path):
    from app.middleware import is_sensitive_path

    assert is_sensitive_path(path)


@pytest.mark.parametrize(
    "path",
    [
        # Começam com /auth mas NÃO são as três rotas protegidas:
        # correspondência exata evita proteger caminhos por engano.
        "/auth",
        "/auth/",
        "/auth/login-extra",
        "/auth/me/extra",
        "/auth/register-v2",
        "/authorization",
        # Outras rotas não sensíveis:
        "/health",
        "/",
        "/docs",
        "/openapi.json",
    ],
)
def test_auth_lookalike_paths_are_not_protected(path):
    from app.middleware import is_sensitive_path

    assert not is_sensitive_path(path)


def test_non_financial_paths_are_out_of_scope():
    from app.middleware import is_sensitive_path

    for path in ("/health", "/", "/docs", "/openapi.json"):
        assert not is_sensitive_path(path)


# ============================================================
# ROTAS DE AUTENTICAÇÃO: token e dados pessoais
# ============================================================

def _register(client, email, password="password123", name="Pedro"):
    return client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": password},
    )


# --- /auth/register ---

def test_register_success_has_no_store(client):
    res = _register(client, "nostore-reg@email.com")
    assert res.status_code == 201
    assert res.headers["cache-control"] == NO_STORE
    # Não deve vazar hash de senha na resposta.
    assert "hashed_password" not in res.text
    assert "password" not in res.json()


def test_register_duplicate_email_has_no_store(client):
    _register(client, "nostore-dup@email.com")
    res = _register(client, "nostore-dup@email.com")
    assert res.status_code == 409
    assert res.headers["cache-control"] == NO_STORE


def test_register_validation_error_has_no_store(client):
    res = client.post(
        "/auth/register",
        json={"name": "X", "email": "nao-e-email", "password": "123"},
    )
    assert res.status_code == 422
    assert res.headers["cache-control"] == NO_STORE


# --- /auth/login ---

def test_login_success_has_no_store(client):
    _register(client, "nostore-login@email.com")
    res = client.post(
        "/auth/login",
        data={"username": "nostore-login@email.com", "password": "password123"},
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE
    assert res.json()["access_token"]


def test_login_wrong_password_has_no_store(client):
    _register(client, "nostore-badpass@email.com")
    res = client.post(
        "/auth/login",
        data={"username": "nostore-badpass@email.com", "password": "errada"},
    )
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


def test_login_unknown_user_has_no_store(client):
    res = client.post(
        "/auth/login", data={"username": "ninguem@email.com", "password": "x"}
    )
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


def test_login_missing_fields_has_no_store(client):
    res = client.post("/auth/login", data={})
    assert res.status_code == 422
    assert res.headers["cache-control"] == NO_STORE


# --- /auth/me ---

def test_me_success_has_no_store(client):
    headers = get_auth_headers(client, "nostore-me@email.com")
    res = client.get("/auth/me", headers=headers)
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE
    assert res.json()["email"] == "nostore-me@email.com"
    assert "hashed_password" not in res.text


def test_me_without_token_has_no_store(client):
    res = client.get("/auth/me")
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


def test_me_invalid_token_has_no_store(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer token-invalido"})
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


def test_me_expired_token_has_no_store(client):
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.security import ALGORITHM, SECRET_KEY

    expired = jwt.encode(
        {
            "sub": "1",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert res.status_code == 401
    assert res.headers["cache-control"] == NO_STORE


# --- CORS continua funcionando nas rotas de auth ---

def test_cors_preserved_on_login(client):
    _register(client, "nostore-cors-auth@email.com")
    res = client.post(
        "/auth/login",
        data={"username": "nostore-cors-auth@email.com", "password": "password123"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == NO_STORE
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_on_login_is_unaffected(client):
    res = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert res.headers.get("cache-control") != NO_STORE
