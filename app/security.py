import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

from typing import Any

import jwt
from jwt import InvalidTokenError
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
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

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


# --- Rate limiting simples em memória para rotas de autenticação ---
# Suficiente para uma instância única (Render free/single). Se a aplicação
# escalar horizontalmente, migrar para um limitador externo (ex.: Redis).
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300
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


def register_auth_attempt(key: str, success: bool) -> None:
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
        if len(window) > _MAX_ATTEMPTS:
            raise RateLimitExceeded()


def record_attempt(key: str) -> None:
    """Registra QUALQUER tentativa (sucesso ou falha), sem limpar a janela.

    Usado em rotas onde o próprio volume de requisições é o abuso
    (ex.: cadastro).
    """
    now = time.monotonic()
    with _attempts_lock:
        window = _touch(key, now)
        window.append(now)
        if len(window) > _MAX_ATTEMPTS:
            raise RateLimitExceeded()


def check_auth_rate_limit(key: str) -> None:
    """Lança RateLimitExceeded se a chave já excedeu o limite."""
    now = time.monotonic()
    with _attempts_lock:
        window = _touch(key, now)
        if len(window) > _MAX_ATTEMPTS:
            raise RateLimitExceeded()
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


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


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
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
