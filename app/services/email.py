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
import re

import httpx

from app import config

logger = logging.getLogger("finance_tracker.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"
BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
TIMEOUT_SECONDS = 10.0
DEFAULT_SENDER_NAME = "Finance Tracker"

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


def _parse_sender(raw: str) -> tuple[str, str]:
    """Interpreta `EMAIL_FROM` como "Nome <email>" ou apenas "email".

    Tolerante de propósito: já houve configuração sem os `<>` em produção
    ("Finance Tracker onboarding@resend.dev"), que o provedor recusava com
    422. Aqui o formato errado é aceito e corrigido, em vez de virar falha
    silenciosa.
    """
    raw = (raw or "").strip()
    match = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", raw)
    if match:
        nome = match.group(1).strip().strip('"').strip() or DEFAULT_SENDER_NAME
        return nome, match.group(2).strip()

    partes = raw.split()
    if len(partes) > 1:
        # Sem <>: o último token é o endereço, o resto é o nome.
        return " ".join(partes[:-1]), partes[-1]
    return DEFAULT_SENDER_NAME, raw


def active_provider() -> str | None:
    """Provedor efetivo, por ordem de preferência.

    Brevo primeiro porque permite verificar um único endereço de e-mail —
    não exige domínio próprio, que é o cenário atual do projeto.
    """
    if (os.getenv("BREVO_API_KEY") or "").strip():
        return "brevo"
    if (os.getenv("RESEND_API_KEY") or "").strip():
        return "resend"
    return None


def _send_via_brevo(
    api_key: str, nome: str, remetente: str, to_email: str, texto: str, html: str
):
    return httpx.post(
        BREVO_ENDPOINT,
        json={
            "sender": {"name": nome, "email": remetente},
            "to": [{"email": to_email}],
            "subject": SUBJECT,
            "textContent": texto,
            "htmlContent": html,
        },
        headers={"api-key": api_key, "accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )


def _send_via_resend(
    api_key: str, nome: str, remetente: str, to_email: str, texto: str, html: str
):
    return httpx.post(
        RESEND_ENDPOINT,
        json={
            "from": f"{nome} <{remetente}>",
            "to": [to_email],
            "subject": SUBJECT,
            "text": texto,
            "html": html,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=TIMEOUT_SECONDS,
    )


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Envia o e-mail com o link. Retorna se o envio foi aceito pelo provedor.

    Nunca lança exceção: a rota que chama esta função responde igual para
    conta existente e inexistente, e uma falha do provedor não pode virar 500
    (o que revelaria que o e-mail existe).
    """
    ttl = config.PASSWORD_RESET_TTL_MINUTES
    provedor = active_provider()
    sender_raw = (os.getenv("EMAIL_FROM") or "").strip()
    nome, remetente = _parse_sender(sender_raw)

    if provedor is None or not remetente:
        if config.IS_DEV_ENV:
            # Em desenvolvimento o link vai para o console: é assim que se
            # testa o fluxo sem caixa de e-mail.
            logger.warning(
                "Provedor de e-mail nao configurado (modo dev). Link de redefinicao: %s",
                reset_link,
            )
        else:
            logger.warning(
                "Provedor de e-mail nao configurado "
                "(BREVO_API_KEY/RESEND_API_KEY/EMAIL_FROM): "
                "e-mail de redefinicao nao enviado."
            )
        return False

    chave = (
        (os.getenv("BREVO_API_KEY") or "").strip()
        if provedor == "brevo"
        else (os.getenv("RESEND_API_KEY") or "").strip()
    )
    texto = _text_body(reset_link, ttl)
    html = _html_body(reset_link, ttl)

    enviar = _send_via_brevo if provedor == "brevo" else _send_via_resend

    try:
        response = enviar(chave, nome, remetente, to_email, texto, html)
    except httpx.HTTPError as exc:
        # Sem detalhes do destinatário no log (evita PII).
        logger.error(
            "Falha de rede ao enviar e-mail de redefinicao: %s", type(exc).__name__
        )
        return False

    if response.status_code >= 400:
        logger.error(
            "Provedor de e-mail recusou o envio (HTTP %s, %s): %s",
            response.status_code,
            provedor,
            _provider_message(response),
        )
        return False

    logger.info("E-mail de redefinicao enviado via %s.", provedor)
    return True


def _mask_address(address: str) -> str:
    """Mascara a parte local de um endereço, preservando o domínio.

    O domínio ajuda a diagnosticar (ex.: remetente de teste vs. domínio
    próprio) sem expor o endereço de quem opera o serviço.
    """
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    return f"***@{domain}"


def _provider_message(response) -> str:
    """Extrai a mensagem de erro do provedor, sem vazar endereços.

    A Resend devolve algo como {"message": "The from address is not valid"}.
    Isso responde "por que falhou" em segundos, em vez de exigir adivinhação.
    """
    try:
        corpo = response.json()
    except Exception:
        texto = (getattr(response, "text", "") or "")[:200]
    else:
        texto = str(corpo.get("message") or corpo)[:200] if isinstance(corpo, dict) else str(corpo)[:200]

    # Nunca expor e-mail completo no log.
    import re

    return re.sub(r"[\w.+-]+@[\w.-]+", lambda m: _mask_address(m.group(0)), texto)


def log_email_configuration() -> None:
    """Registra, na inicialização, se o envio de e-mail está configurado.

    Existe para tornar visível a falha mais comum em produção: variável de
    ambiente não aplicada. Sem este log, o sintoma é "o e-mail não chegou" e a
    causa fica invisível (a rota responde 202 de propósito).
    """
    provedor = active_provider()
    sender_raw = (os.getenv("EMAIL_FROM") or "").strip()
    _, remetente = _parse_sender(sender_raw)

    if provedor and remetente:
        logger.info(
            "E-mail configurado: provedor=%s, remetente=%s",
            provedor.capitalize(),
            _mask_address(remetente),
        )
        return

    logger.warning(
        "E-mail NAO configurado (provedor=%s, EMAIL_FROM=%s): "
        "recuperacao de senha respondera 202 mas nao enviara nada.",
        provedor or "ausente",
        "presente" if remetente else "ausente",
    )
