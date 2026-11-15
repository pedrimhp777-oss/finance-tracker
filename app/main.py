import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.middleware import NoStoreMiddleware
from app.routes import transactions, auth

load_dotenv()

# O esquema do banco é gerenciado EXCLUSIVAMENTE pelo Alembic.
# Ambientes novos: `alembic upgrade head`. Bancos já existentes criados por
# versões anteriores (via create_all): `alembic stamp head` + migrações
# incrementais (ex.: 0002 para os índices de desempenho).

app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None)


# Declaração explícita das origens para permitir envio de tokens/credenciais
app_env = os.getenv("APP_ENV", "development").lower()
frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
origins = [frontend_url] if frontend_url else []
if app_env in {"development", "dev", "local"}:
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

@app.get("/docs", include_in_schema=False)
def scalar_html():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )


