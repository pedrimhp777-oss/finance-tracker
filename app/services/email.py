"""Envio de e-mail — hoje só recuperação de senha.

Decisão de projeto: **nenhum SDK novo**. A API da Resend é um POST JSON
simples e `httpx` já é dependência do projeto.

Sem `RESEND_API_KEY` a função não falha: registra um aviso e devolve `False`.
A rota de recuperação continua respondendo 202 (não vaza existência de conta),
mas o usuário não recebe e-mail — por isso a variável é obrigatória em
produção, e está documentada no README e no `.env.example`.

Regra de privacidade: o **token nunca vai para o log em produção**. Em
desenvolvimento imprimimos o link, porque é como se testa o fluxo sem caixa
de e-mail.
"""
from __future__ import annotations

import logging
import os

import httpx

from app import config

logger = logging.getLogger("finance_tracker.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 10.0

SUBJECT = "Redefinição de senha — Finance Tracker"


def _text_body(reset_link: str, ttl_minutes: int) -> str:
    return (
        "Recebemos um pedido para redefinir a senha da sua conta no Finance Tracker.\n\n"
        f"Acesse o link abaixo para criar uma nova senha:\n{reset_link}\n\n"
        f"Este link expira em {ttl_minutes} minutos e só pode ser usado uma vez.\n\n"
        "Se você não fez este pedido, ignore este e-mail: sua senha continua a mesma."
    )


def _html_body(reset_link: str, ttl_minutes: int) -> str:
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:520px">
  <h2 style="color:#0f172a">Redefinição de senha</h2>
  <p>Recebemos um pedido para redefinir a senha da sua conta no
     <strong>Finance Tracker</strong>.</p>
  <p>
    <a href="{reset_link}"
       style="display:inline-block;background:#10b981;color:#04231a;padding:12px 20px;
              border-radius:8px;text-decoration:none;font-weight:600">
      Criar nova senha
    </a>
  </p>
  <p style="color:#475569;font-size:14px">
    Este link expira em {ttl_minutes} minutos e só pode ser usado uma vez.
  </p>
  <p style="color:#475569;font-size:14px">
    Se você não fez este pedido, ignore este e-mail: sua senha continua a mesma.
  </p>
</div>"""


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Envia o e-mail com o link. Retorna se o envio foi aceito pelo provedor.

    Nunca lança exceção: a rota que chama esta função responde igual para
    conta existente e inexistente, e uma falha do provedor não pode virar 500
    (o que revelaria que o e-mail existe).
    """
    ttl = config.PASSWORD_RESET_TTL_MINUTES
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = (os.getenv("EMAIL_FROM") or "").strip()

    if not api_key or not sender:
        if config.IS_DEV_ENV:
            # Em desenvolvimento o link vai para o console: é assim que se
            # testa o fluxo sem caixa de e-mail.
            logger.warning(
                "RESEND_API_KEY/EMAIL_FROM ausentes (modo dev). Link de redefinição: %s",
                reset_link,
            )
        else:
            logger.warning(
                "RESEND_API_KEY/EMAIL_FROM ausentes: e-mail de redefinição não enviado."
            )
        return False

    payload = {
        "from": sender,
        "to": [to_email],
        "subject": SUBJECT,
        "text": _text_body(reset_link, ttl),
        "html": _html_body(reset_link, ttl),
    }

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        # Sem detalhes do destinatário no log (evita PII).
        logger.error("Falha de rede ao enviar e-mail de redefinição: %s", type(exc).__name__)
        return False

    if response.status_code >= 400:
        logger.error("Provedor de e-mail recusou o envio (HTTP %s).", response.status_code)
        return False

    logger.info("E-mail de redefinição enviado.")
    return True
