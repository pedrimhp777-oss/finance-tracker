import os
from dotenv import load_dotenv

load_dotenv()

# Segurança dos testes: o ambiente de teste SEMPRE usa SQLite em memória.
# Sobrescrevemos (e nunca apenas "setdefault") para garantir que nenhum teste
# acesse o banco configurado no ambiente (produção/Neon).
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
# Chave apenas para testes locais/CI (não é segredo de produção).
# Valor aleatório de 64 hex: precisa passar pelo validador de força que
# rejeita placeholders como "test-only...".
os.environ.setdefault(
    "SECRET_KEY",
    "6d02a011eff5d1c0f15a4f48129f6b53ae4d38a6494d537437388976e49b9f14",
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db

# Banco SQLite em memória com pool estático para manter as tabelas durante os testes
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Isola o estado do rate limiter entre testes (memória global do app)."""
    from app import security
    security._attempts.clear()
    yield
    security._attempts.clear()


@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

