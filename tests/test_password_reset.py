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
from urllib.parse import parse_qs, urlsplit

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
    # O token viaja no fragmento: a query nunca o contém.
    partes = urlsplit(link)
    assert not parse_qs(partes.query).get("reset_token")
    return parse_qs(partes.fragment)["reset_token"][0]


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


# --- Envio fora do caminho da resposta (B2) ---

def test_forgot_agenda_o_envio_em_background(client, monkeypatch):
    """A rota não pode esperar o provedor (timeout de até 10 s).

    Além de segurar a resposta, o tempo denunciava quem tem conta: só a conta
    existente chegava a chamar o provedor.
    """
    from app.routes import auth as auth_routes

    # Sem URL de reset configurada o link nem é montado (nem enviado) — o CI
    # não tem .env, então o teste precisa garantir a variável.
    monkeypatch.setattr(
        auth_routes.config, "PASSWORD_RESET_URL", "https://app.exemplo.com"
    )

    email = "background@email.com"
    get_auth_headers(client, email)  # registra a conta

    agendados = []
    monkeypatch.setattr(
        auth_routes.BackgroundTasks,
        "add_task",
        lambda self, fn, *args, **kwargs: agendados.append((fn, args)),
    )

    res = client.post("/auth/forgot-password", json={"email": email})

    assert res.status_code == 202
    assert len(agendados) == 1, "o envio deveria ter sido agendado, não executado"
    fn, args = agendados[0]
    assert fn is auth_routes.send_password_reset_email
    assert args[0] == email
    assert "reset_token=" in args[1]


def test_forgot_nao_agenda_envio_para_conta_inexistente(client, monkeypatch):
    """Não existindo conta, não há o que enviar — a resposta segue igual."""
    from app.routes import auth as auth_routes

    monkeypatch.setattr(
        auth_routes.config, "PASSWORD_RESET_URL", "https://app.exemplo.com"
    )

    agendados = []
    monkeypatch.setattr(
        auth_routes.BackgroundTasks,
        "add_task",
        lambda self, fn, *args, **kwargs: agendados.append((fn, args)),
    )

    res = client.post("/auth/forgot-password", json={"email": "ninguem@email.com"})

    assert res.status_code == 202
    assert agendados == []


# --- Uso único atômico (B7) ---

def test_reivindicacao_do_token_e_atomica(db):
    """Duas tentativas com o mesmo link: só a primeira consome.

    Antes havia leitura + checagem em Python + escrita; entre elas, dois
    pedidos simultâneos passavam pela checagem e ambos trocavam a senha.
    Agora é um `UPDATE ... WHERE used_at IS NULL`, que informa quantas linhas
    mudou.
    """
    from app.routes import auth as auth_routes

    user = models.User(
        name="Atomic",
        email="atomico@email.com",
        hashed_password="x",
    )
    db.add(user)
    db.commit()

    token = "token-de-teste-atomico"
    db.add(
        models.PasswordResetToken(
            user_id=user.id,
            token_hash=auth_routes._hash_reset_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
    )
    db.commit()

    agora = datetime.now(timezone.utc)
    primeira = auth_routes._claim_reset_token(db, token, agora)
    assert primeira is not None
    db.commit()

    segunda = auth_routes._claim_reset_token(db, token, agora)
    assert segunda is None


def test_token_expirado_nao_e_reivindicado(db):
    from app.routes import auth as auth_routes

    user = models.User(name="Exp", email="expirado@email.com", hashed_password="x")
    db.add(user)
    db.commit()
    token = "token-expirado"
    db.add(
        models.PasswordResetToken(
            user_id=user.id,
            token_hash=auth_routes._hash_reset_token(token),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db.commit()

    assert auth_routes._claim_reset_token(db, token, datetime.now(timezone.utc)) is None


def test_reivindicacao_e_um_update_condicional(db):
    """A garantia contra corrida é o `WHERE used_at IS NULL` no SQL.

    Um teste com threads não serve aqui: o agendamento pode serializar as duas
    tentativas e o teste passaria mesmo com o padrão antigo (ler → checar →
    gravar). O que elimina a janela é a escrita ser condicional, então é isso
    que se verifica.
    """
    from sqlalchemy import event

    from app.routes import auth as auth_routes

    user = models.User(name="SQL", email="sql@email.com", hashed_password="x")
    db.add(user)
    db.commit()
    token = "token-do-sql"
    db.add(
        models.PasswordResetToken(
            user_id=user.id,
            token_hash=auth_routes._hash_reset_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
    )
    db.commit()

    emitidos: list[str] = []

    def capturar(_conn, _cursor, statement, _params, _context, _executemany):
        emitidos.append(" ".join(statement.split()))

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capturar)
    try:
        assert auth_routes._claim_reset_token(db, token, datetime.now(timezone.utc))
    finally:
        event.remove(engine, "before_cursor_execute", capturar)

    updates = [s for s in emitidos if s.upper().startswith("UPDATE PASSWORD_RESET_TOKENS")]
    assert updates, emitidos
    assert "used_at IS NULL" in updates[0]
    assert "expires_at >" in updates[0]


# ------------------------------------------- token no fragmento (F8)

def test_link_envia_o_token_no_fragmento(client, capturar_email):
    """O token não pode ir na query: ela vai para os logs do servidor.

    Um `GET /?reset_token=...` fica registrado no log de acesso, no `Referer`
    e em qualquer proxy entre o usuário e o app. O fragmento não é enviado em
    nenhuma requisição.
    """
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})

    link = capturar_email[0]["link"]
    partes = urlsplit(link)

    assert partes.query == "", f"query não deveria carregar nada: {link}"
    assert partes.fragment.startswith("reset_token=")
    assert "reset_token" not in link.split("#")[0]


def test_link_funciona_de_ponta_a_ponta_pelo_fragmento(client, capturar_email):
    """Ler o token do fragmento e trocar a senha, como o frontend faz."""
    _criar_usuario(client)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_do_link(capturar_email[0]["link"])

    res = client.post(
        "/auth/reset-password", json={"token": token, "new_password": "senha-nova-123"}
    )

    assert res.status_code == 204
    assert (
        client.post("/auth/login", data={"username": EMAIL, "password": "senha-nova-123"}).status_code
        == 200
    )
