from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import get_db

router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_ip(request: Request) -> str:
    """Resolve o IP real do cliente na infraestrutura da Render.

    Cadeia real: Cliente -> Cloudflare (edge da Render) -> proxy da Render -> app.

    1. `CF-Connecting-IP` / `True-Client-Ip`: definidos pelo edge Cloudflare,
       que SOBRESCREVE qualquer valor enviado pelo cliente — não forjáveis.
    2. `X-Forwarded-For`: a Render documenta que o PRIMEIRO IP da lista é o
       cliente real (o valor vindo do cliente é substituído pelo proxy; o
       segundo elemento é o edge IP da Cloudflare, compartilhado).
    3. Fallback: IP de conexão direta (uso local/sem proxy).

    Limitação: se a aplicação for hospedada fora da Render (sem o proxy
    confiável na frente), o primeiro elemento do XFF volta a ser forjável.
    Nesse caso, confiure um proxy de confiança ou remova o fallback.
    """
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip.split(",")[0].strip()
    true_client = request.headers.get("true-client-ip", "").strip()
    if true_client:
        return true_client.split(",")[0].strip()
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    request: Request,
    user: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    # Limita o volume de cadastros por origem (anti-abuso/spam).
    # Cada tentativa conta, mesmo com sucesso, pois o abuso é o volume.
    try:
        security.record_attempt(f"register:{_client_ip(request)}")
    except security.RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em alguns minutos.",
        ) from exc

    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado")

    new_user = models.User(
        name=user.name,
        email=user.email,
        hashed_password=security.hash_password(user.password),
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado") from exc

    db.refresh(new_user)
    return new_user


@router.post("/login")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    ip = _client_ip(request)
    rate_key = f"login:{ip}:{form_data.username.strip().lower()}"
    try:
        security.check_auth_rate_limit(rate_key)
        email = form_data.username.strip().lower()
        user = db.query(models.User).filter(models.User.email == email).first()
        ok = bool(user and security.verify_password(form_data.password, user.hashed_password))
        security.register_auth_attempt(rate_key, ok)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="E-mail ou senha incorretos",
            )
    except security.RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em alguns minutos.",
        ) from exc

    access_token = security.create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(security.get_current_user)):
    return current_user
