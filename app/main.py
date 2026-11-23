import os

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.middleware import (
    NOSNIFF_HEADER,
    NO_STORE_HEADER,
    NoStoreMiddleware,
    SecurityHeadersMiddleware,
    is_sensitive_path,
)
from app.observability import init_sentry
from app.services.email import log_email_configuration
from app.validation import validation_exception_handler
from app.routes import transactions, auth

# O ambiente é carregado por `app.config` (importado acima), que é o primeiro
# módulo a ler variáveis como APP_ENV — carregar aqui seria tarde demais.

# Monitoramento de erros: no-op se SENTRY_DSN nao estiver definido.
# Chamado antes de criar o app para que falhas de import/warmup sejam vistas.
init_sentry()

# Deixa visivel no log se o envio de e-mail esta configurado (a rota de
# recuperacao responde 202 mesmo sem provedor, por design).
log_email_configuration()

# O esquema do banco é gerenciado EXCLUSIVAMENTE pelo Alembic.
# Ambientes novos: `alembic upgrade head`. Bancos já existentes criados por
# versões anteriores (via create_all): `alembic stamp head` + migrações
# incrementais (ex.: 0002 para os índices de desempenho).

app_env = config.APP_ENV
# Ambientes de desenvolvimento/teste expõem o schema OpenAPI; produção não.
# A ausência de APP_ENV cai em "development", então deployments reais devem
# definir APP_ENV=production para fechar /docs e /openapi.json.
is_dev_env = config.IS_DEV_ENV

app = FastAPI(
    title="Finance Tracker API",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json" if is_dev_env else None,
)

frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
origins = [frontend_url] if frontend_url else []
if is_dev_env:
    origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])

ALLOWED_ORIGINS: list[str] = [o for o in origins if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Adicionado DEPOIS do CORS para ser a camada mais externa: assim o header
# final inclui os headers de CORS já calculados e o Cache-Control é aplicado
# por último, sem interferir no preflight.
app.add_middleware(NoStoreMiddleware)

# `nosniff` vale para toda resposta, inclusive as que o NoStoreMiddleware não
# cobre (health, raiz, preflight). Fica por fora para alcançar tudo.
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth.router)
app.include_router(transactions.router)


@app.get("/", include_in_schema=False)
def home():
    return {"message": "API de Finanças rodando com sucesso!"}


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


def apply_cors_headers(request: Request, response: JSONResponse) -> None:
    """Replica os headers de CORS numa resposta gerada fora da pilha de middlewares.

    O `ServerErrorMiddleware` do Starlette é a camada MAIS EXTERNA: ele responde
    antes do `CORSMiddleware`, então um 500 nunca receberia
    `Access-Control-Allow-Origin`. Sem isso o navegador bloqueia a resposta e o
    usuário vê "erro de conexão" em vez da mensagem genérica.

    Só emite os headers quando a origem está na lista permitida — nunca
    refletimos qualquer origem recebida.
    """
    origin = request.headers.get("origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"


# Mensagens de validação em português (o padrão do FastAPI é inglês).
# Registrado antes do handler genérico: o Starlette escolhe o handler mais
# específico pela classe da exceção.
app.add_exception_handler(RequestValidationError, validation_exception_handler)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Resposta genérica para erros não tratados, com `no-store` quando aplicável.

    O `ServerErrorMiddleware` do Starlette responde por fora da pilha de
    middlewares, portanto nem o `NoStoreMiddleware` nem o `CORSMiddleware`
    alcançam este caso. Por isso os headers são aplicados manualmente aqui.

    O corpo é intencionalmente genérico (não vaza stack trace nem detalhes
    internos). O traceback continua sendo registrado pelo servidor ASGI,
    que re-lança a exceção após enviar a resposta.
    """
    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno do servidor"},
    )
    if is_sensitive_path(request.url.path):
        response.headers["Cache-Control"] = NO_STORE_HEADER
    # O 500 é gerado fora da pilha de middlewares, então `nosniff` também
    # precisa ser aplicado aqui.
    response.headers["X-Content-Type-Options"] = NOSNIFF_HEADER
    apply_cors_headers(request, response)
    return response


if is_dev_env:

    @app.get("/docs", include_in_schema=False)
    def scalar_html():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
        )


