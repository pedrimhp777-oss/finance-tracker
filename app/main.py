import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine, Base, get_db
from app.routes import transactions, auth
from app.services import excel_export

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialização da aplicação.

    A criação do schema aqui é idempotente (CREATE IF NOT EXISTS) e serve como
    rede de segurança para ambientes simples. Em produção, a estrutura do banco
    deve ser gerenciada pelo Alembic (ver app/../migrations).
    """
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None, lifespan=lifespan)


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


