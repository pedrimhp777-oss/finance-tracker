"""Testes do header `X-Content-Type-Options: nosniff`.

Por que isso importa numa API JSON: sem `nosniff`, um navegador pode
"adivinhar" o tipo do corpo e tratar como HTML/script algo servido como
`application/json`. O header força o respeito ao `Content-Type` declarado.

Cobertura:
- sucesso, erro do cliente (401/404/422), preflight e rota pública;
- erro 500 gerado fora da pilha de middlewares;
- exportação Excel continua funcionando (StreamingResponse não é bufferizada).
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_transactions import get_auth_headers

NOSNIFF = "nosniff"


def test_nosniff_em_rota_publica(client):
    assert client.get("/health").headers["x-content-type-options"] == NOSNIFF


def test_nosniff_em_rota_raiz(client):
    assert client.get("/").headers["x-content-type-options"] == NOSNIFF


def test_nosniff_em_401(client):
    res = client.get("/auth/me")
    assert res.status_code == 401
    assert res.headers["x-content-type-options"] == NOSNIFF


def test_nosniff_em_404(client):
    headers = get_auth_headers(client, "nosniff-404@email.com")
    res = client.get("/transactions/999999", headers=headers)
    assert res.status_code == 404
    assert res.headers["x-content-type-options"] == NOSNIFF


def test_nosniff_em_422(client):
    res = client.get("/transactions/", params={"month": 13})
    assert res.status_code in (401, 422)
    assert res.headers["x-content-type-options"] == NOSNIFF


def test_nosniff_no_preflight(client):
    res = client.options(
        "/transactions/",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == NOSNIFF
    # O preflight continua sem no-store (cache controlado por Max-Age).
    assert "no-store" not in res.headers.get("cache-control", "")


def test_nosniff_em_500_nao_tratado(client, monkeypatch):
    """O 500 sai fora da pilha de middlewares, então é aplicado no handler."""
    from app.services import transactions as service

    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(service, "filtered_transactions", boom)
    headers = get_auth_headers(client, "nosniff-500@email.com")
    raw = TestClient(app, raise_server_exceptions=False)

    res = raw.get("/transactions/", headers=headers)
    assert res.status_code == 500
    assert res.headers["x-content-type-options"] == NOSNIFF


def test_exportacao_excel_continua_integra(client):
    """`StreamingResponse` não pode ser bufferizada pelo middleware ASGI."""
    headers = get_auth_headers(client, "nosniff-excel@email.com")
    client.post(
        "/transactions/",
        json={
            "description": "planilha",
            "amount": 10,
            "type": "expense",
            "category": "Outros",
        },
        headers=headers,
    )
    res = client.get("/transactions/export/excel", headers=headers)

    assert res.status_code == 200
    assert res.headers["x-content-type-options"] == NOSNIFF
    # Assinatura ZIP (xlsx é um arquivo zip).
    assert res.content[:2] == b"PK"
    assert len(res.content) > 1000
