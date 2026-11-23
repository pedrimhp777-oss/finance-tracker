"""Testes de token inválido/expirado, /auth/me e rate limiting."""
import pytest

from app import config, security
from app.routes import auth as auth_routes

# Teto fixo de iterações nos laços de rate limit: usar o próprio limite como
# contador faz o teste girar para sempre se o limite for afrouxado.
TETO_TENTATIVAS = 200
from test_transactions import get_auth_headers


# --- /auth/me ---

def test_get_me_success(client):
    headers = get_auth_headers(client, "me@email.com")
    res = client.get("/auth/me", headers=headers)
    assert res.status_code == 200
    assert res.json()["email"] == "me@email.com"


def test_get_me_invalid_token(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer token-invalido"})
    assert res.status_code == 401


def test_get_me_expired_token(client):
    import jwt

    from app.security import ALGORITHM, SECRET_KEY

    client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "exp@email.com", "password": "password123"},
    )
    # Assinatura VÁLIDA (mesma SECRET_KEY da app) com exp no passado:
    # o 401 precisa vir da expiração, não de assinatura inválida.
    token = jwt.encode(
        {"sub": "1", "exp": 0},  # expirado em 1970
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_get_me_missing_sub(client):
    import jwt

    from app.security import ALGORITHM, SECRET_KEY

    # Assinatura válida, `exp` válido e `sub` ausente.
    token = jwt.encode(
        {"exp": 9999999999},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# --- Rate limiting ---

def test_login_rate_limit_after_repeated_failures(client):
    client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "ratelimit@email.com", "password": "password123"},
    )
    email = "ratelimit@email.com"
    status_codes = []
    for _ in range(15):
        res = client.post(
            "/auth/login", data={"username": email, "password": "errada"}
        )
        status_codes.append(res.status_code)
        if res.status_code == 429:
            break

    # Depois de muitas tentativas, o limite deve ser acionado.
    assert 429 in status_codes


# --- Enumeração de contas por tempo de resposta (B2) ---

def test_login_roda_bcrypt_mesmo_sem_conta(client, monkeypatch):
    """E-mail inexistente não pode responder mais rápido que e-mail existente.

    Antes havia curto-circuito (`user and verify_password(...)`): sem conta, o
    bcrypt não rodava e a resposta saía na hora — medindo o tempo, dava para
    descobrir quem tem cadastro.
    """
    chamadas = []
    original = security.verify_password

    def espiao(senha, hash_armazenado):
        chamadas.append(hash_armazenado)
        return original(senha, hash_armazenado)

    monkeypatch.setattr(security, "verify_password", espiao)
    res = client.post(
        "/auth/login",
        data={"username": "nao-existe-mesmo@email.com", "password": "errada"},
    )

    assert res.status_code == 401
    assert chamadas == [security.DUMMY_PASSWORD_HASH]


def test_login_roda_bcrypt_uma_vez_quando_a_conta_existe(client, monkeypatch):
    """Contraprova: com conta, o hash verificado é o real (e só ele)."""
    client.post(
        "/auth/register",
        json={"name": "X", "email": "existe@email.com", "password": "password123"},
    )
    chamadas = []
    original = security.verify_password

    def espiao(senha, hash_armazenado):
        chamadas.append(hash_armazenado)
        return original(senha, hash_armazenado)

    monkeypatch.setattr(security, "verify_password", espiao)
    res = client.post(
        "/auth/login", data={"username": "existe@email.com", "password": "errada"}
    )

    assert res.status_code == 401
    assert len(chamadas) == 1
    assert chamadas[0] != security.DUMMY_PASSWORD_HASH
    assert chamadas[0].startswith("$2b$")


def test_hash_falso_tem_o_mesmo_custo_dos_hashes_reais():
    """Custo diferente faria o tempo voltar a diferenciar os dois casos."""
    custo_falso = security.DUMMY_PASSWORD_HASH.split("$")[2]
    custo_real = security.hash_password("qualquer-senha").split("$")[2]
    assert custo_falso == custo_real


def test_hash_falso_nao_autentica_ninguem():
    assert security.verify_password("", security.DUMMY_PASSWORD_HASH) is False
    assert security.verify_password("password123", security.DUMMY_PASSWORD_HASH) is False


# --- Rate limit: buckets separados por IP e por e-mail (B3) ---

def test_login_limita_por_email_mesmo_vindo_de_ips_diferentes(client, monkeypatch):
    """Ataque distribuído contra UMA conta.

    Cada IP cria um par (ip, e-mail) novo, então o bucket do par nunca enche —
    quem barra é o bucket por e-mail.
    """
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(security, "verify_password", lambda *_: False)
    email = "alvo-distribuido@email.com"
    codigos = []

    for i in range(TETO_TENTATIVAS):
        res = client.post(
            "/auth/login",
            data={"username": email, "password": "errada"},
            headers={"CF-Connecting-IP": f"203.0.113.{i}"},
        )
        codigos.append(res.status_code)
        if res.status_code == 429:
            break

    assert codigos[-1] == 429
    assert codigos.count(401) == security.LOGIN_EMAIL_LIMIT


def test_login_limita_por_ip_mesmo_com_emails_diferentes(client, monkeypatch):
    """Credential stuffing: muitos e-mails a partir de um único IP."""
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(security, "verify_password", lambda *_: False)
    codigos = []

    for i in range(TETO_TENTATIVAS):
        res = client.post(
            "/auth/login",
            data={"username": f"stuffing{i}@email.com", "password": "errada"},
            headers={"CF-Connecting-IP": "198.51.100.9"},
        )
        codigos.append(res.status_code)
        if res.status_code == 429:
            break

    assert codigos[-1] == 429


def test_bucket_por_ip_fica_desligado_quando_o_ip_nao_e_do_cliente(client, monkeypatch):
    """Sem TRUST_PROXY_HEADERS em produção, o IP visto é o do proxy.

    Manter o bucket nesse cenário juntaria TODOS os clientes na mesma chave, e
    um atacante bloquearia o login de todo mundo com algumas dezenas de falhas.
    """
    monkeypatch.setattr(config, "IS_DEV_ENV", False)
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(security, "verify_password", lambda *_: False)

    for i in range(TETO_TENTATIVAS):
        res = client.post(
            "/auth/login", data={"username": f"compartilhado{i}@email.com", "password": "x"}
        )
        assert res.status_code == 401, f"bloqueou indevidamente na tentativa {i + 1}"


def test_limite_por_par_continua_valendo(client):
    """O bucket original (IP + e-mail) segue sendo o mais restrito."""
    email = "par@email.com"
    client.post(
        "/auth/register",
        json={"name": "P", "email": email, "password": "password123"},
    )
    codigos = []
    for _ in range(TETO_TENTATIVAS):
        res = client.post("/auth/login", data={"username": email, "password": "errada"})
        codigos.append(res.status_code)
        if res.status_code == 429:
            break
    assert codigos[-1] == 429
    assert codigos.count(401) == security.LOGIN_PAIR_LIMIT


# --- Sessão apenas por Bearer (B5: cookie removido) ---

def test_login_nao_emite_cookie_de_sessao(client):
    """A sessão é um token no corpo; nenhum cookie é definido.

    O modo cookie + CSRF existia como alternativa opt-in e foi removido: sem
    frontend e API no mesmo domínio registrável, o navegador descartaria o
    cookie de qualquer forma.
    """
    client.post(
        "/auth/register",
        json={"name": "Sem", "email": "semcookie@email.com", "password": "password123"},
    )
    res = client.post(
        "/auth/login",
        data={"username": "semcookie@email.com", "password": "password123"},
    )

    assert res.status_code == 200
    assert "access_token" in res.json()
    assert not [h for h in res.headers if h.lower() == "set-cookie"]


def test_cookie_com_token_valido_nao_autentica(client):
    """Contraprova: o token só vale no header, nunca em cookie."""
    headers = get_auth_headers(client, "soheader@email.com")
    token = headers["Authorization"].split(" ", 1)[1]

    client.cookies.set("access_token", token)
    res = client.get("/auth/me")

    assert res.status_code == 401
