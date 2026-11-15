"""Testes da resolução do IP real do cliente (infraestrutura Render).

Cadeia real documentada: Cliente -> Cloudflare (edge da Render) -> proxy da
Render -> app. Referências:
- https://render.com/articles/how-render-handles-ddos-attacks (oficial)
- https://feedback.render.com/features/p/send-the-correct-x-forwarded-for
  ("we set the first IP in the list to the real client IP")
"""
import pytest
from fastapi import Request

from app.routes.auth import _client_ip


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


def _request(headers: dict[str, str], host: str = "10.21.157.68") -> Request:
    """Constrói um Request fake com o IP de conexão do proxy da Render."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
        "query_string": b"",
        "client": (host, 50000),
    }
    return Request(scope)


# --- CF-Connecting-IP (fonte preferencial, definida pelo edge Cloudflare) ---

def test_cf_connecting_ip_wins_over_forged_headers():
    req = _request({
        "CF-Connecting-IP": "99.61.165.29",
        "X-Forwarded-For": "6.6.6.6, 104.22.17.40",  # forjado pelo cliente
        "True-Client-Ip": "7.7.7.7",
    })
    assert _client_ip(req) == "99.61.165.29"


def test_true_client_ip_used_when_cf_missing():
    req = _request({
        "True-Client-Ip": "99.61.165.29",
        "X-Forwarded-For": "6.6.6.6, 104.22.17.40",
    })
    assert _client_ip(req) == "99.61.165.29"


# --- X-Forwarded-For (Render coloca o cliente real no PRIMEIRO elemento) ---

def test_xff_first_element_is_client():
    """Exemplo real da cadeia Render: cliente, depois edge Cloudflare."""
    req = _request({"X-Forwarded-For": "99.61.165.29, 104.22.17.40"})
    assert _client_ip(req) == "99.61.165.29"


def test_xff_forged_values_do_not_take_priority_over_cf():
    req = _request({
        "CF-Connecting-IP": "203.0.113.50",
        "X-Forwarded-For": "8.8.8.8, 9.9.9.9, 104.22.17.40",
    })
    assert _client_ip(req) == "203.0.113.50"


# --- Ausência dos cabeçalhos confiáveis ---

def test_no_headers_falls_back_to_socket_ip():
    req = _request({})
    assert _client_ip(req) == "10.21.157.68"


def test_empty_header_values_fall_back_to_socket_ip():
    req = _request({"CF-Connecting-IP": "", "X-Forwarded-For": "   "})
    assert _client_ip(req) == "10.21.157.68"


# --- Integração: o rate limit deve agrupar por IP REAL, não forjado ---

def test_rate_limit_uses_real_ip_not_forged(client):
    """Muitas tentativas com XFF forjado devem ser limitadas porque o
    CF-Connecting-IP (definido pelo edge) domina a chave."""
    client.post(
        "/auth/register",
        json={"name": "P", "email": "ipreal@email.com", "password": "password123"},
    )
    statuses = []
    for _ in range(15):
        res = client.post(
            "/auth/login",
            data={"username": "ipreal@email.com", "password": "errada"},
            headers={"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "6.6.6.6"},
        )
        statuses.append(res.status_code)
        if res.status_code == 429:
            break
    assert 429 in statuses
