import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import models
from app.database import get_db

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY precisa ter ao menos 32 caracteres para iniciar a API.")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


# --- Rate limiting simples em memória para rotas de autenticação ---
# Suficiente para uma instância única (Render free/single). Se a aplicação
# escalar horizontalmente, migrar para um limitador externo (ex.: Redis).
_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300
_attempts_lock = threading.Lock()
_attempts: dict[str, deque[float]] = defaultdict(deque)


class RateLimitExceeded(Exception):
    """Sinaliza excesso de tentativas para uma chave."""


def register_auth_attempt(key: str, success: bool) -> None:
    """Registra uma tentativa; falhas consecutivas elevam o contador.

    Sucessos limpam o histórico da chave para não bloquear usuários legítimos.
    """
    now = time.monotonic()
    with _attempts_lock:
        window = _attempts[key]
        if success:
            window.clear()
            return
        window.append(now)
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()
        if len(window) > _MAX_ATTEMPTS:
            raise RateLimitExceeded()


def check_auth_rate_limit(key: str) -> None:
    """Lança RateLimitExceeded se a chave já excedeu o limite."""
    now = time.monotonic()
    with _attempts_lock:
        window = _attempts[key]
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()
        if len(window) > _MAX_ATTEMPTS:
            raise RateLimitExceeded()



def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
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
    except (JWTError, TypeError, ValueError):
        raise credentials_exception

    if user is None:
        raise credentials_exception
    return user
