"""Monitoramento de erros (Sentry) — opcional e sem PII.

Por que existe: hoje o traceback de um 500 morre no log efêmero da Render.
Sem isso, um erro em produção é invisível até um usuário reclamar.

Princípio central: **o monitoramento nunca é mais sensível que a aplicação.**
Esta é uma API financeira. Um evento de erro pode carregar corpo de requisição
(senha em `/auth/login`, valores e descrições em `/transactions`) e headers de
autenticação (token do usuário). Nada disso pode sair do servidor.

Camadas de proteção (defesa em profundidade, não uma só):

1. `max_request_body_size="never"` — o SDK não coleta corpo de requisição;
2. `send_default_pii=False` — sem IP, cookies ou headers de usuário por padrão;
3. `before_send` — remove corpo/cookies/query string e mascara headers
   sensíveis que ainda assim apareçam no evento;
4. `before_breadcrumb` — limpa payloads em breadcrumbs (HTTP, log, console).

Se `SENTRY_DSN` não estiver definido, `init_sentry()` não faz nada e a
aplicação segue exatamente como antes: nenhuma chamada de rede, nenhum
overhead.
"""
from __future__ import annotations

import os
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

# Headers que jamais podem sair do servidor.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-csrf-token",
        "x-api-key",
        "x-auth-token",
    }
)

# Chaves de dados que podem conter conteúdo do usuário.
SENSITIVE_KEYS = frozenset(
    {"body", "payload", "data", "password", "token", "access_token", "csrf_token"}
)

FILTERED = "[Filtered]"


def _mask_headers(headers: Any) -> None:
    if isinstance(headers, dict):
        for key in list(headers):
            if key.lower() in SENSITIVE_HEADERS:
                headers[key] = FILTERED


def scrub_event(event: dict, hint: dict | None = None) -> dict:
    """Remove dados pessoais e financeiros de um evento antes do envio."""
    request = event.get("request")
    if isinstance(request, dict):
        # Corpo pode conter senha ou valores financeiros.
        request.pop("data", None)
        request.pop("cookies", None)
        # Termo de busca (`?search=...`) também é dado do usuário.
        request.pop("query_string", None)
        url = request.get("url")
        if isinstance(url, str) and "?" in url:
            request["url"] = url.split("?", 1)[0]
        _mask_headers(request.get("headers"))

    # `user` nunca é preenchido por nós, mas o SDK poderia anexar identificador.
    if "user" in event:
        event["user"] = {"id": None}

    for crumb in (event.get("breadcrumbs") or {}).get("values") or []:
        _scrub_breadcrumb_data(crumb)

    return event


def _scrub_breadcrumb_data(crumb: dict) -> None:
    data = crumb.get("data")
    if isinstance(data, dict):
        for key in list(data):
            lowered = key.lower()
            if lowered in SENSITIVE_KEYS or lowered in SENSITIVE_HEADERS:
                data[key] = FILTERED
        _mask_headers(data.get("headers"))


def scrub_breadcrumb(crumb: dict, hint: dict | None = None) -> dict | None:
    """Limpa breadcrumbs (requisições HTTP, logs) antes de anexar ao evento."""
    _scrub_breadcrumb_data(crumb)
    return crumb


def init_sentry() -> bool:
    """Inicializa o Sentry se `SENTRY_DSN` existir. Retorna se inicializou.

    Não lança exceção: monitoramento nunca pode derrubar a aplicação.
    """
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False

    try:
        sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
    except ValueError:
        sample_rate = 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT") or os.getenv("APP_ENV") or "production",
        release=os.getenv("RENDER_GIT_COMMIT") or os.getenv("SENTRY_RELEASE") or None,
        # 1) Nada de PII automática.
        send_default_pii=False,
        # 2) Corpo de requisição nunca é coletado.
        max_request_body_size="never",
        # 3) Rede de segurança.
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        # Performance desligada por padrão (custo/travamento de query).
        traces_sample_rate=sample_rate,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
    )
    return True
