"""CORS em respostas de erro não tratado (HTTP 500).

Contexto: o `ServerErrorMiddleware` do Starlette é a camada MAIS EXTERNA da
pilha. Ele responde ANTES do `CORSMiddleware`, então um 500 gerado pelo
`@app.exception_handler(Exception)` sairia sem `Access-Control-Allow-Origin`.
O navegador bloquearia a resposta e o usuário veria "erro de conexão" em vez
da mensagem genérica — justamente no caso em que mais precisamos de clareza.

Estes testes travam o comportamento:
- origem permitida  -> headers de CORS presentes (e sem refletir outras origens);
- origem não permitida -> nenhum header de CORS;
- sem header Origin -> nenhum header de CORS;
- `no-store` continua sendo aplicado só em rotas sensíveis.
"""
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from test_transactions import get_auth_headers

ALLOWED = "https://app.example.com"
NOT_ALLOWED = "https://evil.example"


@pytest.fixture
def raw_client(client):
    """Cliente que devolve a resposta 500 em vez de re-lançar a exceção.

    Depende de `client` só para garantir que o override de `get_db` (Postgres
    de teste) esteja ativo enquanto o 500 é provocado.
    """
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def forced_500(monkeypatch):
    """Faz a rota financeira levantar erro inesperado, e fixa as origens."""
    from app.services import transactions as service

    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada no serviço")

    monkeypatch.setattr(service, "filtered_transactions", boom)
    monkeypatch.setattr(main_module, "ALLOWED_ORIGINS", [ALLOWED])
    return ALLOWED


def _lower(headers) -> dict:
    return {k.lower(): v for k, v in headers.items()}


def test_500_com_origem_permitida_recebe_cors(raw_client, forced_500):
    headers = get_auth_headers(raw_client, "cors-ok@email.com")
    res = raw_client.get("/transactions/", headers={**headers, "Origin": ALLOWED})

    assert res.status_code == 500
    h = _lower(res.headers)
    assert h["access-control-allow-origin"] == ALLOWED
    assert h["access-control-allow-credentials"] == "true"
    assert "origin" in h.get("vary", "").lower()
    # Escopo de cache preservado no mesmo caminho.
    assert h["cache-control"] == "no-store"
    # Corpo genérico, sem vazar detalhes internos.
    assert "falha simulada" not in res.text


def test_500_com_origem_nao_permitida_nao_recebe_cors(raw_client, forced_500):
    headers = get_auth_headers(raw_client, "cors-bad@email.com")
    res = raw_client.get("/transactions/", headers={**headers, "Origin": NOT_ALLOWED})

    assert res.status_code == 500
    h = _lower(res.headers)
    # Nunca refletimos uma origem arbitrária.
    assert "access-control-allow-origin" not in h


def test_500_sem_origem_nao_recebe_cors(raw_client, forced_500):
    headers = get_auth_headers(raw_client, "cors-none@email.com")
    res = raw_client.get("/transactions/", headers=headers)

    assert res.status_code == 500
    assert "access-control-allow-origin" not in _lower(res.headers)


def test_500_em_rota_publica_nao_recebe_no_store(raw_client, forced_500):
    """O `no-store` continua restrito ao escopo financeiro/auth."""
    res = raw_client.get("/health", headers={"Origin": ALLOWED})

    assert res.status_code in (200, 500)
    assert "no-store" not in _lower(res.headers).get("cache-control", "")


def test_aplicador_de_cors_ignora_origem_vazia():
    """Guarda-corpo: sem Origin, nada é emitido (não gera header vazio)."""
    from starlette.requests import Request
    from fastapi.responses import JSONResponse

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    response = JSONResponse({"detail": "x"}, status_code=500)
    main_module.apply_cors_headers(Request(scope), response)
    assert "access-control-allow-origin" not in response.headers
