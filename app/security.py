import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

from typing import Any

import bcrypt
import jwt
from jwt import InvalidTokenError
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

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


def _configured_bcrypt_rounds() -> int:
    """Custo do bcrypt (log2 das iterações). 12 em produção.

    Os testes usam 4 (`BCRYPT_ROUNDS=4` no conftest): com 12, cada cadastro
    custa ~250 ms e a suíte inteira levava mais de três minutos só hasheando.
    O valor é lido do ambiente — nunca reduzido por conta própria.
    """
    raw = os.getenv("BCRYPT_ROUNDS", "12").strip()
    try:
        rounds = int(raw)
    except ValueError:
        raise RuntimeError("BCRYPT_ROUNDS precisa ser um número inteiro.") from None
    if not 4 <= rounds <= 31:
        raise RuntimeError("BCRYPT_ROUNDS precisa estar entre 4 e 31.")
    return rounds


BCRYPT_ROUNDS = _configured_bcrypt_rounds()

# Hash de uma senha aleatória que ninguém conhece, com o MESMO custo dos hashes
# reais. O login verifica a senha contra ele quando o e-mail NÃO existe: sem
# isso a rota respondia na hora para e-mail inexistente e só gastava o custo do
# bcrypt quando a conta existia, permitindo enumerar contas pelo tempo de
# resposta. Gerado no import (custa um hash por boot) para acompanhar
# automaticamente o custo configurado — um valor fixo mentiria se o custo
# mudasse e reabriria a brecha.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    os.urandom(32), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
).decode("ascii")

# Único modo de sessão. `auto_error=False`: a ausência do header vira o mesmo
# 401 de token inválido, sem revelar qual credencial faltou.
bearer_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

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
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


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
    bearer_token: str | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """Autentica pelo header `Authorization: Bearer`.

    Só este modo existe: a sessão via cookie foi removida porque exigiria
    frontend e API no mesmo domínio registrável, e sem isso o navegador
    descarta o cookie (third-party). Ver README, seção Autenticação.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not bearer_token:
        raise credentials_exception
    token = bearer_token

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

    return user
