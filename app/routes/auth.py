import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config, models, schemas, security
from app.database import get_db
from app.services.email import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_ip(request: Request) -> str:
    """IP do cliente, confiando em cabeçalhos de proxy só quando habilitado.

    Cadeia na Render: Cliente -> Cloudflare (edge) -> proxy da Render -> app.

    Com `TRUST_PROXY_HEADERS=true` (obrigatório na Render):
    1. `CF-Connecting-IP` / `True-Client-Ip`: o edge Cloudflare sobrescreve
       qualquer valor vindo do cliente;
    2. `X-Forwarded-For`: a Render põe o cliente real no PRIMEIRO elemento;
    3. IP da conexão TCP.

    Com a flag desligada (padrão) devolve apenas o IP da conexão: cabeçalho
    enviado pelo cliente é forjável, e um `X-Forwarded-For` falso daria uma
    identidade nova a cada requisição, anulando o rate limit por IP.
    """
    if config.TRUST_PROXY_HEADERS:
        for header in ("cf-connecting-ip", "true-client-ip", "x-forwarded-for"):
            value = request.headers.get(header, "").strip()
            if value:
                return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_is_trustworthy() -> bool:
    """O bucket de rate limit por IP só vale quando o IP é do cliente.

    Sem `TRUST_PROXY_HEADERS` em produção, `request.client.host` é o proxy:
    TODOS os clientes cairiam no mesmo bucket e um atacante conseguiria
    bloquear o login de todo mundo. Nesse cenário o bucket por IP é desligado;
    os buckets por e-mail e por (IP + e-mail) continuam valendo.
    """
    return config.TRUST_PROXY_HEADERS or config.IS_DEV_ENV


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
    email = form_data.username.strip().lower()
    pair_key = f"login:{ip}:{email}"
    email_key = f"login-email:{email}"
    ip_key = f"login-ip:{ip}"
    # O bucket por IP é o único que depende de saber o IP real do cliente.
    trusted_ip = _ip_is_trustworthy()

    try:
        # Três buckets porque cada ataque tem uma forma: um IP contra uma
        # conta, muitos IPs contra uma conta, e um IP contra muitas contas.
        security.check_auth_rate_limit(pair_key, security.LOGIN_PAIR_LIMIT)
        security.check_auth_rate_limit(email_key, security.LOGIN_EMAIL_LIMIT)
        if trusted_ip:
            security.check_auth_rate_limit(ip_key, security.LOGIN_IP_LIMIT)

        user = db.query(models.User).filter(models.User.email == email).first()

        # O bcrypt roda MESMO quando o e-mail não existe. Antes havia um
        # curto-circuito (`user and verify_password(...)`): e-mail inexistente
        # respondia na hora e a conta existente gastava ~250 ms, o que
        # permitia descobrir quem tem conta só medindo o tempo.
        stored_hash = user.hashed_password if user else security.DUMMY_PASSWORD_HASH
        password_ok = security.verify_password(form_data.password, stored_hash)

        ok = password_ok and user is not None
        security.register_auth_attempt(pair_key, ok, security.LOGIN_PAIR_LIMIT)
        if not ok:
            # Falhas também contam nos buckets amplos; sucesso NÃO limpa esses
            # dois (senão uma conta válida zeraria o limite do IP inteiro).
            security.record_attempt(email_key, security.LOGIN_EMAIL_LIMIT)
            if trusted_ip:
                security.record_attempt(ip_key, security.LOGIN_IP_LIMIT)
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
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Revoga as sessões do usuário no servidor.

    Incrementa `token_version`, invalidando qualquer token emitido antes
    deste instante — inclusive cópias obtidas por terceiros. Funciona com
    múltiplas instâncias e sobrevive a reinícios, pois o estado vive no banco.
    """
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
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
    background_tasks: BackgroundTasks,
    payload: schemas.ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """Envia o link de redefinição — sempre com a mesma resposta.

    Um 404 para e-mail inexistente revelaria quais contas existem.

    O envio vai para `BackgroundTasks`: antes a rota chamava o provedor
    sincronicamente (até 10 s de timeout) apenas quando a conta existia, e esse
    tempo — não a resposta — denunciava quem tem cadastro. Com o envio fora do
    caminho da resposta, a diferença restante entre conta existente e
    inexistente é um INSERT de milissegundos.
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
            # Enviado depois da resposta: o provedor pode levar segundos.
            background_tasks.add_task(send_password_reset_email, user.email, link)

    # Resposta genérica: mesmo corpo, mesmo status, exista ou não a conta.
    return {
        "detail": "Se existir uma conta com este e-mail, enviamos um link de redefinição."
    }


def _claim_reset_token(
    db: Session, token: str, now: datetime
) -> models.PasswordResetToken | None:
    """Consome o token de forma atômica; devolve None se não for utilizável.

    `UPDATE ... WHERE used_at IS NULL` informa quantas linhas mudaram: só uma
    requisição pode ganhar. Antes o código lia o registro, checava `used_at` em
    Python e só depois gravava — entre a leitura e a escrita, dois pedidos
    simultâneos com o mesmo link passavam pela checagem e ambos trocavam a
    senha. Expiração também entra no próprio WHERE, pelo mesmo motivo.
    """
    token_hash = _hash_reset_token(token)
    claimed = (
        db.query(models.PasswordResetToken)
        .filter(
            models.PasswordResetToken.token_hash == token_hash,
            models.PasswordResetToken.used_at.is_(None),
            models.PasswordResetToken.expires_at > now,
        )
        .update({models.PasswordResetToken.used_at: now}, synchronize_session=False)
    )
    if claimed != 1:
        db.rollback()
        return None
    return (
        db.query(models.PasswordResetToken)
        .filter(models.PasswordResetToken.token_hash == token_hash)
        .one()
    )


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

    registro = _claim_reset_token(db, payload.token, datetime.now(timezone.utc))
    if registro is None:
        raise invalid

    user = db.query(models.User).filter(models.User.id == registro.user_id).first()
    if user is None:
        db.rollback()
        raise invalid

    user.hashed_password = security.hash_password(payload.new_password)
    # Revoga sessões antigas: se a conta estava comprometida, o token do
    # invasor morre junto com a troca de senha.
    user.token_version = (user.token_version or 0) + 1

    # Uso único de fato: nenhum outro pedido pendente continua válido.
    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.id,
        models.PasswordResetToken.id != registro.id,
    ).delete(synchronize_session=False)

    db.commit()
    return None
