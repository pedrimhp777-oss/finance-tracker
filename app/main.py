import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference
from dotenv import load_dotenv

from app.database import engine, Base
from app import models
from app.routes import transactions, auth

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None)

# Declaração explicita das origens para permitir envio de tokens/credenciais
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    os.getenv("FRONTEND_URL", "")
]

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

@app.get("/docs", include_in_schema=False)
def scalar_html():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )