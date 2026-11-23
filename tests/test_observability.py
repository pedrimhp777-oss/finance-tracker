"""Testes do monitoramento de erros — o que NUNCA pode sair do servidor.

Esta é uma API financeira. Um evento de erro pode carregar:
- senha em `/auth/login` (corpo do formulário);
- descrição, categoria e valor de transações;
- o JWT do usuário no header `Authorization`;
- o termo de busca financeira na query string.

O scrubbing é testado aqui de forma direta: montamos eventos no formato do
Sentry e verificamos que os dados sensíveis são removidos ou mascarados.
"""
import pytest

from app import observability


@pytest.fixture(autouse=True)
def _no_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)


# ---------------------------------------------------------------- init

def test_sem_dsn_nao_inicializa_nada():
    assert observability.init_sentry() is False


def test_dsn_vazio_nao_inicializa(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "   ")
    assert observability.init_sentry() is False


# ---------------------------------------------------------------- corpo

def test_corpo_da_requisicao_e_removido():
    event = {
        "request": {
            "data": {"username": "p@email.com", "password": "senha-secreta"},
            "url": "https://api/auth/login",
        }
    }
    evento = observability.scrub_event(event)
    assert "data" not in evento["request"]
    assert "senha-secreta" not in str(evento)


def test_corpo_com_valor_financeiro_e_removido():
    event = {
        "request": {
            "data": {"description": "aluguel", "amount": "2500.00", "category": "Moradia"},
        }
    }
    evento = observability.scrub_event(event)
    assert "data" not in evento["request"]
    assert "2500.00" not in str(evento)


# ---------------------------------------------------------------- headers

def test_authorization_e_cookie_sao_mascarados():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.abc",
                "Cookie": "access_token=segredo; csrf_token=xyz",
                "X-CSRF-Token": "abc123",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            }
        }
    }
    evento = observability.scrub_event(event)
    headers = evento["request"]["headers"]

    assert headers["Authorization"] == observability.FILTERED
    assert headers["Cookie"] == observability.FILTERED
    assert headers["X-CSRF-Token"] == observability.FILTERED
    # Headers inofensivos permanecem (ajudam a diagnosticar).
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == "Mozilla/5.0"
    assert "eyJhbGci" not in str(evento)


def test_mascaramento_ignora_caixa_do_header():
    event = {"request": {"headers": {"AUTHORIZATION": "Bearer x", "COOKIE": "y"}}}
    evento = observability.scrub_event(event)
    assert evento["request"]["headers"]["AUTHORIZATION"] == observability.FILTERED
    assert evento["request"]["headers"]["COOKIE"] == observability.FILTERED


# ---------------------------------------------------------------- URL

def test_query_string_e_removida():
    """`?search=...` pode conter termo financeiro pessoal."""
    event = {
        "request": {
            "url": "https://api/transactions/?search=aluguel&month=11",
            "query_string": "search=aluguel&month=11",
        }
    }
    evento = observability.scrub_event(event)
    assert "query_string" not in evento["request"]
    assert evento["request"]["url"] == "https://api/transactions/"
    assert "aluguel" not in str(evento)


def test_url_sem_query_permanece_intacta():
    event = {"request": {"url": "https://api/transactions/"}}
    evento = observability.scrub_event(event)
    assert evento["request"]["url"] == "https://api/transactions/"


# ---------------------------------------------------------------- cookies

def test_cookies_do_request_sao_removidos():
    event = {"request": {"cookies": "access_token=segredo"}}
    evento = observability.scrub_event(event)
    assert "cookies" not in evento["request"]
    assert "segredo" not in str(evento)


# ---------------------------------------------------------------- usuário

def test_dados_do_usuario_sao_apagados():
    event = {"user": {"id": 7, "email": "p@email.com", "ip_address": "1.2.3.4"}}
    evento = observability.scrub_event(event)
    assert evento["user"] == {"id": None}
    assert "p@email.com" not in str(evento)


# ---------------------------------------------------------------- breadcrumbs

def test_breadcrumb_com_header_sensivel_e_limpo():
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "httplib",
                    "data": {
                        "url": "https://api/auth/login",
                        "headers": {"Authorization": "Bearer tokenzinho"},
                        "body": {"password": "x"},
                        "status_code": 500,
                    },
                }
            ]
        }
    }
    evento = observability.scrub_event(event)
    data = evento["breadcrumbs"]["values"][0]["data"]

    assert data["headers"]["Authorization"] == observability.FILTERED
    assert data["body"] == observability.FILTERED
    assert "tokenzinho" not in str(evento)
    # Dado útil para diagnóstico permanece.
    assert data["status_code"] == 500


def test_scrub_breadcrumb_isolado():
    crumb = {"category": "log", "data": {"token": "segredo", "message": "ok"}}
    limpo = observability.scrub_breadcrumb(crumb)
    assert limpo["data"]["token"] == observability.FILTERED
    assert limpo["data"]["message"] == "ok"


# ---------------------------------------------------------------- robustez

def test_evento_sem_request_nao_quebra():
    """Monitoramento nunca pode lançar exceção por causa de um formato inesperado."""
    assert observability.scrub_event({}) == {}
    assert observability.scrub_event({"request": None}) == {"request": None}
    assert observability.scrub_event({"request": {"headers": None}})["request"] == {
        "headers": None
    }


def test_evento_vazio_ou_estranho_nao_quebra():
    for event in ({}, {"breadcrumbs": {}}, {"breadcrumbs": {"values": None}}, {"request": "x"}):
        assert isinstance(observability.scrub_event(event), dict)
