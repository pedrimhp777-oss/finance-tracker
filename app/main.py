import os

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from scalar_fastapi import get_scalar_api_reference
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.middleware import NO_STORE_HEADER, NoStoreMiddleware, is_sensitive_path
from app.routes import transactions, auth

load_dotenv()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in origins if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Adicionado DEPOIS do CORS para ser a camada mais externa: assim o header
# final inclui os headers de CORS já calculados e o Cache-Control é aplicado
# por último, sem interferir no preflight.
app.add_middleware(NoStoreMiddleware)

app.include_router(auth.router)
app.include_router(transactions.router)


@app.get("/", include_in_schema=False)
def home():
    return {"message": "API de Finanças rodando com sucesso!"}


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Resposta genérica para erros não tratados, com `no-store` quando aplicável.

    O `ServerErrorMiddleware` do Starlette responde por fora da pilha de
    middlewares, portanto o `NoStoreMiddleware` não alcança este caso. Sem
    este handler, um 500 em rota financeira sairia sem `Cache-Control`.

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
    return response


if is_dev_env:

    @app.get("/docs", include_in_schema=False)
    def scalar_html():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
        )


