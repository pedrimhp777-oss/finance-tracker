import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

from typing import Any

import hmac
import bcrypt
import jwt
from jwt import InvalidTokenError
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import config

from app import models
from app.database import get_db

load_dotenv()

# --- Validação de força da SECRET_KEY -----------------------------------
# IMPORTANTE: estas checagens rejeitam chaves OBVIAMENTE fracas (curtas,
# vazias, placeholders, repetitivas). Elas NÃO medem entropia criptográfica
# real — nenhuma verificação estática mede. A chave deve ser gerada por um
# CSPRNG, por exemplo:
#
#     python -c "import secrets; print(secrets.token_urlsafe(48))"
#
# Nenhum valor de chave é registrado em log ou incluído em mensagens de erro.
MIN_SECRET_KEY_LENGTH = 32
# Poucos caracteres distintos indica chave previsível ("aaaa...", "12341234...").
MIN_DISTINCT_CHARACTERS = 8
# Valores que aparecem em exemplos/documentação e NUNCA devem ir a produção.
_PLACEHOLDER_MARKERS = (
    "generate-a-long-random",
    "your-secret-key",
    "your_secret_key",
    "changeme",
    "change-me",
    "change_me",
    "not-for-production",
    "never-use-in-production",
    "never_use_in_production",
    "test-only",
    "ci-only",
    "secret-key-here",
    "replace-me",
    "replace_me",
    "placeholder",
    "example-secret",
    "supersecret",
)


def validate_secret_key(value: str | None) -> str:
    """Valida a força mínima da SECRET_KEY e devolve o valor original.

    Não altera nem substitui a chave: apenas rejeita valores inaceitáveis.
    As mensagens nunca contêm a chave, só a regra violada.
    """
    if not value or not value.strip():
        raise RuntimeError(
            "SECRET_KEY precisa estar definida e não pode ser vazia ou conter "
            "apenas espaços."
        )
    if len(value) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY precisa ter ao menos {MIN_SECRET_KEY_LENGTH} caracteres."
        )
    lowered = value.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            raise RuntimeError(
                "SECRET_KEY parece um valor de exemplo/placeholder. Gere uma "
                'chave real com: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )
    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        raise RuntimeError(
            "SECRET_KEY parece fraca: poucos caracteres distintos. Gere uma "
            'chave real com: python -c "import secrets; '
            'print(secrets.token_urlsafe(48))"'
        )
    return value


# A chave é lida e validada uma única vez, no import do módulo: se for
# inaceitável, a API não inicia.
SECRET_KEY = validate_secret_key(os.getenv("SECRET_KEY"))
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# bcrypt direto, sem passlib. O passlib 1.7.4 (última release, 2020) está
# sem manutenção e QUEBRA com bcrypt >= 4.1: ele lê `bcrypt.__about__`, que foi
# removido, e não lida com o limite de 72 bytes virar exceção. Manter passlib
# obrigava a fixar bcrypt==4.0.1, bloqueando atualizações de segurança do
# próprio bcrypt. A API usada aqui (`hashpw`/`checkpw`) é estável e os hashes
# no formato `$2b$` gerados pelo passlib continuam válidos.
BCRYPT_MAX_PASSWORD_BYTES = 72

# Hash de uma senha aleatória que ninguém conhece, com o mesmo custo (12) dos
# hashes reais. O login verifica a senha contra ele quando o e-mail NÃO existe:
# sem isso a rota respondia na hora para e-mail inexistente e só gastava o
# custo do bcrypt quando a conta existia, permitindo enumerar contas pelo tempo
# de resposta. Fixado como constante para não pagar o custo em cada boot.
# `test_password_hashing.py` garante que o custo acompanha `hash_password`.
DUMMY_PASSWORD_HASH = (
    "$2b$12$S7ikU2xd9RTPgFsJHbFwU.F5WefRbJJTNi54I3p3FadK3BqGOfYdy"
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
# Variante que não falha sozinha: permite cair para o cookie de sessão.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# Métodos que não alteram estado — não exigem token CSRF.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def enforce_csrf(request: Request) -> None:
    """Valida o double-submit de CSRF para requisições autenticadas por cookie.

    Só é chamado quando a autenticação veio do cookie (o header `Authorization`
    já imuniza contra CSRF, pois um site terceiro não consegue defini-lo em
    formulários). O token CSRF vive em cookie legível por JS e precisa ser
    ecoado no header `X-CSRF-Token`.
    """
    if request.method in SAFE_METHODS:
        return
    cookie_token = request.cookies.get(config.CSRF_COOKIE_NAME) or ""
    header_token = request.headers.get(config.CSRF_HEADER_NAME) or ""
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requisição bloqueada: token CSRF ausente.",
        )
    if not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requisição bloqueada: token CSRF inválido.",
        )


# --- Rate limiting simples em memória para rotas de autenticação ---
# Suficiente para uma instância única (Render free/single). Se a aplicação
# escalar horizontalmente, migrar para um limitador externo (ex.: Redis).
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300
# Limites por bucket no login. Cada ataque tem uma forma diferente:
#   par (IP + e-mail) -> força bruta contra UMA conta a partir de UM IP;
#   e-mail            -> a mesma conta atacada por MUITOS IPs (distribuído);
#   IP                -> credential stuffing, muitos e-mails de UM IP.
LOGIN_PAIR_LIMIT = _MAX_ATTEMPTS
LOGIN_EMAIL_LIMIT = 20
LOGIN_IP_LIMIT = 60
# Limita o número de chaves rastreadas para impedir crescimento ilimitado
# da memória (atacante gerando e-mails/IPs distintos).
_MAX_KEYS = 10_000
_attempts_lock = threading.Lock()
# OrderedDict: menos usadas são descartadas quando _MAX_KEYS é atingido.
_attempts: OrderedDict[str, deque[float]] = OrderedDict()


class RateLimitExceeded(Exception):
    """Sinaliza excesso de tentativas para uma chave."""


def _evict_if_needed() -> None:
    """Descarta as chaves menos recentemente usadas acima do limite.

    Precisa ser chamado com _attempts_lock segurado.
    """
    while len(_attempts) > _MAX_KEYS:
        _attempts.popitem(last=False)


def _touch(key: str, now: float) -> deque[float]:
    """Obtém/cria a janela da chave e a marca como recentemente usada."""
    window = _attempts.pop(key, None)
    if window is None:
        window = deque()
    _attempts[key] = window
    while window and now - window[0] > _WINDOW_SECONDS:
        window.popleft()
    _evict_if_needed()
    return window


def register_auth_attempt(key: str, success: bool, limit: int = _MAX_ATTEMPTS) -> None:
    """Registra uma tentativa; falhas consecutivas elevam o contador.

    Sucessos limpam o histórico da chave para não bloquear usuários legítimos.
    """
    now = time.monotonic()
    with _attempts_lock:
        window = _touch(key, now)
        if success:
            window.clear()
            return
        window.append(now)
        if len(window) > limit:
            raise RateLimitExceeded()


def record_attempt(key: str, limit: int = _MAX_ATTEMPTS) -> None:
    """Registra QUALQUER tentativa (sucesso ou falha), sem limpar a janela.

    Usado em rotas onde o próprio volume de requisições é o abuso
    (ex.: cadastro).
    """
    now = time.monotonic()
    with _attempts_lock:
        window = _touch(key, now)
        window.append(now)
        if len(window) > limit:
            raise RateLimitExceeded()


def check_auth_rate_limit(key: str, limit: int = _MAX_ATTEMPTS) -> None:
    """Lança RateLimitExceeded se a chave já excedeu o limite."""
    now = time.monotonic()
    with _attempts_lock:
        window = _touch(key, now)
        if len(window) > limit:
            raise RateLimitExceeded()
def hash_password(password: str) -> str:
    """Gera o hash bcrypt da senha.

    Recusa explicitamente senhas acima de 72 bytes em vez de truncar: duas
    senhas que diferem apenas depois do 72º byte produziriam o mesmo hash
    (no bcrypt antigo o truncamento era silencioso).
    """
    payload = password.encode("utf-8")
    if len(payload) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"A senha excede o limite de {BCRYPT_MAX_PASSWORD_BYTES} bytes do bcrypt."
        )
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Confere a senha contra o hash. Nunca levanta exceção: retorna False.

    Hash corrompido, formato desconhecido ou senha acima de 72 bytes viram
    falha de autenticação — não erro 500. Isso também preserva o tempo de
    resposta: uma exceção vazaria que "aquele usuário" tem hash inválido.
    """
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def create_access_token(data: dict) -> str:
    """Emite um JWT levando junto a versão de sessão do usuário (`tv`).

    O `tv` é confrontado com `users.token_version` em `get_current_user`.
    Incrementar o contador no banco invalida todos os tokens anteriores.
    """
    to_encode: dict[str, Any] = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    to_encode.setdefault("tv", 0)
    # PyJWT serializa `exp` (datetime) como timestamp e exige `sub` string.
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    request: Request,
    bearer_token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> models.User:
    """Autentica via header `Authorization: Bearer` ou cookie `HttpOnly`.

    O header tem precedência. Quando a autenticação vem do cookie, aplica-se
    a verificação de CSRF nos métodos que alteram estado.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if bearer_token:
        token = bearer_token
        authenticated_via_cookie = False
    else:
        token = request.cookies.get(config.AUTH_COOKIE_NAME) or ""
        authenticated_via_cookie = True
        if not token:
            raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    except (InvalidTokenError, TypeError, ValueError):
        raise credentials_exception

    if user is None:
        raise credentials_exception

    # Revogação de sessão: tokens emitidos antes do último logout (ou troca de
    # senha) carregam um `tv` menor e são recusados. Tokens legados, sem `tv`,
    # valem como 0 — seguem válidos até o usuário fazer logout.
    token_version = payload.get("tv", 0)
    if not isinstance(token_version, int) or isinstance(token_version, bool):
        raise credentials_exception
    if token_version != (user.token_version or 0):
        raise credentials_exception

    if authenticated_via_cookie:
        enforce_csrf(request)

    return user
