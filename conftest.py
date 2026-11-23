import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Banco de teste: um PostgreSQL de verdade, o mesmo motor de produção.
#
# Antes a suíte rodava em SQLite em memória, e por isso as consultas com fuso
# horário, GROUP BY e tipos tinham dois caminhos no serviço: um testado, outro
# (o que roda em produção) não. Um bug de agregação passou pelos testes e só
# apareceu no deploy. Agora existe um caminho só, exercitado aqui.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/finance_tracker_test",
)

# Sobrescrevemos (e nunca apenas "setdefault") para garantir que nenhum teste
# acesse o banco configurado no ambiente (produção/Neon).
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# A suíte roda como desenvolvimento: o padrão do app é produção (docs fechados,
# cookie `Secure`) e os testes de cookie dependem de HTTP puro.
os.environ["APP_ENV"] = "development"
# Chave apenas para testes locais/CI (não é segredo de produção).
# Valor aleatório de 64 hex: precisa passar pelo validador de força que
# rejeita placeholders como "test-only...".
os.environ.setdefault(
    "SECRET_KEY",
    "6d02a011eff5d1c0f15a4f48129f6b53ae4d38a6494d537437388976e49b9f14",
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import get_db

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Ordem irrelevante: CASCADE resolve as dependências.
APPLICATION_TABLES = ("password_reset_tokens", "transactions", "users")


def _truncate_application_tables() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(f"TRUNCATE {', '.join(APPLICATION_TABLES)} RESTART IDENTITY CASCADE")
        )


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Aplica as migrações reais no Postgres de teste.

    O esquema vem do Alembic (e não de `metadata.create_all`) para que os
    testes também validem 0001→0004 de ponta a ponta.

    Roda em subprocesso pelo mesmo comando usado no deploy: invocar
    `command.upgrade` dentro do processo faria o `fileConfig` do env.py
    reconfigurar o logging do pytest e derrubar o `caplog`.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parent,
        check=True,
        capture_output=True,
        text=True,
    )
    yield


@pytest.fixture(scope="function")
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        _truncate_application_tables()


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
