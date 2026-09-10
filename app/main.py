from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from scalar_fastapi import get_scalar_api_reference

from app.database import engine, Base
from app import models
from app.routes import transactions, auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None)

# Libera o acesso para qualquer origem (necessário para o Frontend consumir a API)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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