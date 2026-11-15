"""Testes da recuperação de senha.

O que está em jogo aqui:
- **não revelar quais contas existem** (enumeração);
- o token em claro nunca ser gravado no banco;
- uso único, expiração e invalidação ao pedir outro link;
- após a troca, TODAS as sessões caírem (o motivo do reset pode ser invasão);
- rate limit contra varredura e mail bombing.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app import models
from app.services import email as email_service
from app.services.email import send_password_reset_email
from test_transactions import get_auth_headers

EMAIL = "reset@email.com"
SENHA = "senha-antiga-123"
NOVA = "senha-nova-456"


@pytest.fixture
def capturar_email(monkeypatch):
    """Captura o link enviado sem depender de provedor de e-mail."""
    enviados = []

    def fake(to_email, link):
        enviados.append({"to": to_email, "link": link})
        return True

    monkeypatch.setattr("app.routes.auth.send_password_reset_email", fake)
    monkeypatch.setattr(
        "app.config.PASSWORD_RESET_URL", "https://app.exemplo.com", raising=False
    )
    return enviados


def _token_do_link(link: str) -> str:
    return parse_qs(urlparse(link).query)["reset_token"][0]


def _criar_usuario(client):
    client.post(
        "/auth/register",
        json={"name": "Reset", "email": EMAIL, "password": SENHA},
    )


# ------------------------------------------------------------------ forgot

def test_forgot_com_conta_existente_gera_link(client, capturar_email):
    _criar_usuario(client)
    res = client.post("/auth/forgot-password", json={"email": EMAIL})

    assert res.status_code == 202
    assert len(capturar_email) == 1
    assert capturar_email[0]["to"] == EMAIL
    assert "reset_token=" in capturar_email[0]["link"]
    assert capturar_email[0]["link"].startswith("https://app.exemplo.com/")


def test_forgot_sem_conta_responde_igual(client, capturar_email):
    """Resposta idêntica impede descobrir quais e-mails estão cadastrados."""
    _criar_usuario(client)
    com_conta = client.post("/auth/forgot-password", json={"email": EMAIL})
    sem_conta = client.post("/auth/forgot-password", json={"email": "naoexiste@email.com"})

    assert com_conta.status_code == sem_conta.status_code == 202
    assert com_conta.json() == sem_conta.json()
    # Nenhum e-mail é enviado para conta inexistente.
    assert len(capturar_email) == 1


def test_forgot_normaliza_email(client, capturar_email):
    _criar_usuario(client)
    res = client.post("/auth/forgot-password", json={"email": "  RESET@Email.COM "})
    assert res.status_code == 202
    assert len(capturar_email) == 1


def test_token_em_claro_nao_e_gravado(client, db, capturar_email):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})

    token = _token_do_link(capturar_email[0]["link"])
    registros = db.query(models.PasswordResetToken).all()
    assert len(registros) == 1

    gravado = registros[0].token_hash
    assert gravado != token
    assert len(gravado) == 64
    assert gravado == hashlib.sha256(token.encode()).hexdigest()


def test_novo_pedido_invalida_o_anterior(client, db, capturar_email):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    primeiro = _token_do_link(capturar_email[0]["link"])

    client.post("/auth/forgot-password", json={"email": EMAIL})
    segundo = _token_do_link(capturar_email[1]["link"])

    assert db.query(models.PasswordResetToken).count() == 1
    # O link antigo deixa de funcionar.
    res = client.post("/auth/reset-password", json={"token": primeiro, "new_password": NOVA})
    assert res.status_code == 400
    # O novo funciona.
    res = client.post("/auth/reset-password", json={"token": segundo, "new_password": NOVA})
    assert res.status_code == 204


def test_forgot_tem_rate_limit(client):
    _criar_usuario(client)
    codigos = [
        client.post("/auth/forgot-password", json={"email": EMAIL}).status_code
        for _ in range(12)
    ]
    assert 429 in codigos


# ------------------------------------------------------------------ reset

def test_reset_troca_a_senha(client, capturar_email):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])

    res = client.post("/auth/reset-password", json={"token": token, "new_password": NOVA})
    assert res.status_code == 204

    # Senha nova entra.
    novo = client.post("/auth/login", data={"username": EMAIL, "password": NOVA})
    assert novo.status_code == 200
    # Senha antiga não entra mais.
    antigo = client.post("/auth/login", data={"username": EMAIL, "password": SENHA})
    assert antigo.status_code == 401


def test_reset_revoga_sessoes_antigas(client, capturar_email):
    """Se a conta foi invadida, o token do invasor morre na troca de senha."""
    _criar_usuario(client)
    headers_antigos = get_auth_headers(client, EMAIL, SENHA)
    assert client.get("/auth/me", headers=headers_antigos).status_code == 200

    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])
    client.post("/auth/reset-password", json={"token": token, "new_password": NOVA})

    assert client.get("/auth/me", headers=headers_antigos).status_code == 401


def test_token_nao_pode_ser_reutilizado(client, capturar_email):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])

    assert client.post(
        "/auth/reset-password", json={"token": token, "new_password": NOVA}
    ).status_code == 204
    # Segunda tentativa com o mesmo link.
    assert client.post(
        "/auth/reset-password", json={"token": token, "new_password": "outra-senha-789"}
    ).status_code == 400


def test_token_expirado_e_recusado(client, db, capturar_email):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])

    registro = db.query(models.PasswordResetToken).first()
    registro.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    res = client.post("/auth/reset-password", json={"token": token, "new_password": NOVA})
    assert res.status_code == 400


def test_token_invalido_e_recusado(client):
    res = client.post(
        "/auth/reset-password",
        json={"token": "a" * 43, "new_password": NOVA},
    )
    assert res.status_code == 400


@pytest.mark.parametrize(
    "senha",
    [
        "curta",  # < 8
        "a" * 73,  # > 72 bytes (bcrypt)
    ],
)
def test_senha_invalida_e_rejeitada(client, capturar_email, senha):
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])

    res = client.post("/auth/reset-password", json={"token": token, "new_password": senha})
    assert res.status_code == 422


# ------------------------------------------------------------------ e-mail

def test_sem_provedor_nao_envia_e_nao_quebra(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    assert send_password_reset_email("x@email.com", "https://app/reset?t=1") is False


def test_falha_de_rede_nao_propaga(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_teste")
    monkeypatch.setenv("EMAIL_FROM", "no-reply@exemplo.com")

    import httpx

    def boom(*args, **kwargs):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(email_service.httpx, "post", boom)
    assert send_password_reset_email("x@email.com", "https://app/reset?t=1") is False


def test_erro_do_provedor_nao_propaga(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_teste")
    monkeypatch.setenv("EMAIL_FROM", "no-reply@exemplo.com")

    class Resposta:
        status_code = 422

    monkeypatch.setattr(email_service.httpx, "post", lambda *a, **k: Resposta())
    assert send_password_reset_email("x@email.com", "https://app/reset?t=1") is False


def test_link_nao_vaza_no_log_de_producao(monkeypatch, caplog):
    """Em produção o token nunca pode ir para o log."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr("app.config.IS_DEV_ENV", False, raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    with caplog.at_level("DEBUG"):
        send_password_reset_email("x@email.com", "https://app/reset?reset_token=SEGREDO123")

    assert "SEGREDO123" not in caplog.text


# ------------------------------------------------------------------ diagnóstico

def test_mascara_endereco_preserva_dominio():
    assert email_service._mask_address("pedro@gmail.com") == "***@gmail.com"
    assert email_service._mask_address("sem-arroba") == "***"


def test_mensagem_do_provedor_e_extraida_e_mascarada():
    """O log precisa dizer POR QUE falhou, sem expor endereços."""

    class Resposta:
        status_code = 422

        def json(self):
            return {"message": "The from address pedro@exemplo.com is not valid"}

        text = ""

    msg = email_service._provider_message(Resposta())
    assert "not valid" in msg
    assert "pedro@exemplo.com" not in msg
    assert "***@exemplo.com" in msg


def test_mensagem_do_provedor_nao_quebra_com_corpo_invalido():
    class Resposta:
        status_code = 500
        text = "<html>erro</html>"

        def json(self):
            raise ValueError("nao e json")

    assert "erro" in email_service._provider_message(Resposta())


def test_log_de_configuracao_avisa_quando_falta_variavel(monkeypatch, caplog):
    """Este log existe para tornar visível 'variável não aplicada na Render'."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)

    with caplog.at_level("WARNING"):
        email_service.log_email_configuration()

    assert "NAO configurado" in caplog.text
    assert "ausente" in caplog.text


def test_log_de_configuracao_confirma_quando_presente(monkeypatch, caplog):
    monkeypatch.setenv("RESEND_API_KEY", "re_teste")
    monkeypatch.setenv("EMAIL_FROM", "Finance Tracker <onboarding@resend.dev>")

    with caplog.at_level("INFO"):
        email_service.log_email_configuration()

    assert "configurado" in caplog.text
    # O endereço completo nunca aparece no log.
    assert "onboarding@resend.dev" not in caplog.text
    assert "***@resend.dev" in caplog.text


# ------------------------------------------------------------------ Brevo

def test_brevo_tem_preferencia_sobre_resend(monkeypatch):
    """Brevo primeiro: permite verificar um endereço, sem exigir domínio."""
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-teste")
    monkeypatch.setenv("RESEND_API_KEY", "re_teste")
    assert email_service.active_provider() == "brevo"


def test_resend_e_usado_quando_nao_ha_brevo(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setenv("RESEND_API_KEY", "re_teste")
    assert email_service.active_provider() == "resend"


def test_sem_nenhuma_chave_nao_ha_provedor(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert email_service.active_provider() is None


def test_brevo_envia_payload_e_header_corretos(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-teste")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("EMAIL_FROM", "Finance Tracker <no-reply@gmail.com>")
    capturado = {}

    class Resposta:
        status_code = 201

    def fake_post(url, **kwargs):
        capturado["url"] = url
        capturado["json"] = kwargs.get("json")
        capturado["headers"] = kwargs.get("headers")
        return Resposta()

    monkeypatch.setattr(email_service.httpx, "post", fake_post)

    assert send_password_reset_email("dest@email.com", "https://app/?reset_token=abc") is True

    assert capturado["url"] == "https://api.brevo.com/v3/smtp/email"
    assert capturado["headers"]["api-key"] == "xkeysib-teste"
    assert capturado["json"]["sender"] == {
        "name": "Finance Tracker",
        "email": "no-reply@gmail.com",
    }
    assert capturado["json"]["to"] == [{"email": "dest@email.com"}]
    # Brevo usa textContent/htmlContent (não text/html).
    assert "textContent" in capturado["json"] and "htmlContent" in capturado["json"]
    assert "reset_token=abc" in capturado["json"]["textContent"]


def test_brevo_com_erro_nao_propaga(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-teste")
    monkeypatch.setenv("EMAIL_FROM", "no-reply@gmail.com")

    class Resposta:
        status_code = 401
        text = ""

        def json(self):
            return {"message": "Key not found"}

    monkeypatch.setattr(email_service.httpx, "post", lambda *a, **k: Resposta())
    assert send_password_reset_email("dest@email.com", "https://app/?reset_token=abc") is False


@pytest.mark.parametrize(
    "bruto,esperado",
    [
        ("Finance Tracker <onboarding@resend.dev>", ("Finance Tracker", "onboarding@resend.dev")),
        # Formato SEM <> que quebrou em produção: agora é tolerado.
        ("Finance Tracker onboarding@resend.dev", ("Finance Tracker", "onboarding@resend.dev")),
        ("a@b.com", ("Finance Tracker", "a@b.com")),
        ("  Nome  < x@y.com >  ", ("Nome", "x@y.com")),
    ],
)
def test_parser_do_remetente_tolera_formatos(bruto, esperado):
    assert email_service._parse_sender(bruto) == esperado
