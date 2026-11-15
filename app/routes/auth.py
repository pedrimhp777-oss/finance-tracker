import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config, models, schemas, security
from app.database import get_db
from app.services.email import send_password_reset_email

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
    response: Response,
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

    access_token = security.create_access_token(
        data={"sub": str(user.id), "tv": user.token_version or 0}
    )

    # Sessão em cookie HttpOnly (não legível por JS, imune a roubo via XSS).
    _set_session_cookies(response, access_token)

    # O token continua no corpo durante a transição: clientes que ainda usam
    # `Authorization: Bearer` seguem funcionando sem alteração.
    return {"access_token": access_token, "token_type": "bearer"}


def _set_session_cookies(response: Response, access_token: str) -> None:
    """Emite o cookie de sessão (HttpOnly) e o token CSRF (legível por JS)."""
    max_age = security.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=access_token,
        max_age=max_age,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key=config.CSRF_COOKIE_NAME,
        value=secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,  # o frontend precisa ler para ecoar no header
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    """Remove os cookies de sessão e CSRF."""
    for name in (config.AUTH_COOKIE_NAME, config.CSRF_COOKIE_NAME):
        response.delete_cookie(
            key=name,
            path="/",
            secure=config.COOKIE_SECURE,
            samesite=config.COOKIE_SAMESITE,
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Revoga as sessões do usuário no servidor.

    Incrementa `token_version`, invalidando qualquer token emitido antes
    deste instante — inclusive cópias obtidas por terceiros. Funciona com
    múltiplas instâncias e sobrevive a reinícios, pois o estado vive no banco.
    Os cookies de sessão e CSRF também são apagados.
    """
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    _clear_session_cookies(response)
    return None


@router.get("/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(security.get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Recuperação de senha
# ---------------------------------------------------------------------------
# Regras que sustentam a segurança destas duas rotas:
#
# 1. NENHUMA enumeração de conta: `forgot-password` responde 202 idêntico
#    exista ou não o e-mail, e não informa se o envio ocorreu.
# 2. O token em claro só existe no e-mail; o banco guarda SHA-256.
# 3. Uso único (`used_at`), expiração curta (30 min) e invalidação de TODAS as
#    sessões após a troca (`token_version`), pois o motivo do reset pode ser
#    justamente uma conta comprometida.
# 4. Rate limit por IP e por e-mail: evita varredura de contas e "mail bombing".

PASSWORD_RESET_TTL_MINUTES = config.PASSWORD_RESET_TTL_MINUTES


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reset_link(token: str) -> str:
    base = config.PASSWORD_RESET_URL or ""
    if not base:
        # Sem URL configurada o e-mail seria inútil; o chamador trata.
        return ""
    return f"{base}/?reset_token={quote(token)}"


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    request: Request,
    payload: schemas.ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """Envia o link de redefinição — sempre com a mesma resposta.

    Um 404 para e-mail inexistente revelaria quais contas existem.
    """
    ip = _client_ip(request)
    email = str(payload.email).strip().lower()

    # `record_attempt` registra E aplica o limite. Usar apenas
    # `check_auth_rate_limit` aqui seria inócuo: sem registrar, a janela
    # ficaria sempre vazia e o limite nunca dispararia.
    try:
        security.record_attempt(f"forgot-ip:{ip}")
        security.record_attempt(f"forgot-email:{email}")
    except security.RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas solicitações. Tente novamente em alguns minutos.",
        ) from exc

    user = db.query(models.User).filter(models.User.email == email).first()

    if user is not None:
        now = datetime.now(timezone.utc)

        # Invalida pedidos anteriores deste usuário: só o link mais recente vale.
        db.query(models.PasswordResetToken).filter(
            models.PasswordResetToken.user_id == user.id,
            models.PasswordResetToken.used_at.is_(None),
        ).delete(synchronize_session=False)

        token = secrets.token_urlsafe(32)
        db.add(
            models.PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_reset_token(token),
                expires_at=now + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES),
            )
        )
        db.commit()

        link = _reset_link(token)
        if link:
            send_password_reset_email(user.email, link)

    # Resposta genérica: mesmo corpo, mesmo status, exista ou não a conta.
    return {
        "detail": "Se existir uma conta com este e-mail, enviamos um link de redefinição."
    }


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    request: Request,
    payload: schemas.ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """Troca a senha usando um token válido e revoga todas as sessões."""
    try:
        security.record_attempt(f"reset-ip:{_client_ip(request)}")
    except security.RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em alguns minutos.",
        ) from exc

    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Link inválido ou expirado. Solicite um novo.",
    )

    registro = (
        db.query(models.PasswordResetToken)
        .filter(models.PasswordResetToken.token_hash == _hash_reset_token(payload.token))
        .first()
    )
    if registro is None or registro.used_at is not None:
        raise invalid

    expira = registro.expires_at
    if expira.tzinfo is None:  # SQLite devolve naive; o banco grava UTC.
        expira = expira.replace(tzinfo=timezone.utc)
    if expira <= datetime.now(timezone.utc):
        raise invalid

    user = db.query(models.User).filter(models.User.id == registro.user_id).first()
    if user is None:
        raise invalid

    user.hashed_password = security.hash_password(payload.new_password)
    # Revoga sessões antigas: se a conta estava comprometida, o token do
    # invasor morre junto com a troca de senha.
    user.token_version = (user.token_version or 0) + 1
    registro.used_at = datetime.now(timezone.utc)

    # Uso único de fato: nenhum outro pedido pendente continua válido.
    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.id != registro.id,
    ).delete(synchronize_session=False)

    db.commit()
    return None
