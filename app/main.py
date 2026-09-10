from fastapi import FastAPI
from scalar_fastapi import get_scalar_api_reference

from app.database import engine, Base
from app import models
from app.routes import transactions, auth

Base.metadata.create_all(bind=engine)

# Desativa o Swagger (/docs) e ReDoc (/redoc) padrões
app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None)

app.include_router(auth.router)
app.include_router(transactions.router)

@app.get("/", include_in_schema=False)
def home():
    return {"message": "API de Finanças rodando com sucesso!"}

# Coloca o Scalar direto na URL /docs
@app.get("/docs", include_in_schema=False)
def scalar_html():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )