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
    ],
)
def test_sensitive_prefix_matches_all_financial_paths(path):
    from app.middleware import SENSITIVE_PATH_PREFIXES

    assert path.startswith(SENSITIVE_PATH_PREFIXES)


def test_non_financial_paths_are_out_of_scope():
    from app.middleware import SENSITIVE_PATH_PREFIXES

    for path in ("/health", "/", "/auth/login", "/auth/me", "/docs"):
        assert not path.startswith(SENSITIVE_PATH_PREFIXES)
