"""Testes do utilitário de backup lógico (scripts/backup.py).

O script é a única defesa contra perder a conta do provedor do banco, então
precisa de teste: aqui ele é exercitado contra SQLite em memória — nunca
contra o banco real.

O que se verifica:
- o dump contém as tabelas esperadas e as contagens declaradas na meta;
- `Decimal` vira string (não float) para não perder centavos;
- datas viram ISO 8601;
- `bytes` viram base64;
- o hash de senha é preservado (sem ele o backup não restaura login);
- `--verify` detecta arquivo íntegro E arquivo corrompido.
"""
import gzip
import importlib.util
import json
import pathlib
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
_SPEC = importlib.util.spec_from_file_location(
    "backup_script", REPO_ROOT / "scripts" / "backup.py"
)
backup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup)


def _engine_with_data():
    """SQLite em memória com users, transactions e alembic_version."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "hashed_password TEXT, token_version INTEGER, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE transactions (id INTEGER PRIMARY KEY, description TEXT, "
                "amount NUMERIC, type TEXT, category TEXT, date DATETIME, user_id INTEGER)"
            )
        )
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        conn.execute(
            text(
                "INSERT INTO users VALUES (1, 'Pedro', 'p@email.com', "
                "'$2b$12$abcdefghijklmnopqrstuv', 0, '2026-11-15 10:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions VALUES (1, 'aluguel', 700.25, 'expense', "
                "'Moradia', '2026-11-15 11:00:00', 1)"
            )
        )
        conn.execute(text("INSERT INTO alembic_version VALUES ('0003_token_version')"))
    return engine


def test_dump_gera_arquivo_com_contagens(tmp_path):
    path = backup._dump(_engine_with_data(), tmp_path)

    assert path.exists()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)

    assert doc["meta"]["row_counts"]["users"] == 1
    assert doc["meta"]["row_counts"]["transactions"] == 1
    assert doc["meta"]["alembic_version"] == "0003_token_version"
    assert doc["meta"]["format_version"] == 1


def test_decimal_vira_string_para_preservar_centavos():
    assert backup._jsonable(Decimal("700.25")) == "700.25"
    # float perderia precisão em valores grandes; string não.
    assert isinstance(backup._jsonable(Decimal("30005501.00")), str)


def test_dump_preserva_hash_e_datas(tmp_path):
    path = backup._dump(_engine_with_data(), tmp_path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)

    user = doc["tables"]["users"][0]
    assert user["hashed_password"].startswith("$2b$")
    # Sem o hash o backup não restaura login — é o dado mais crítico.
    assert len(user["hashed_password"]) > 20
    # A data existe e é legível (no SQLite vem como texto; no PostgreSQL vem
    # como datetime e passa por _jsonable -> ISO 8601).
    assert "2026-11-15" in str(doc["tables"]["transactions"][0]["date"])


def test_datetime_vira_iso_8601():
    from datetime import datetime, timezone

    valor = backup._jsonable(datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc))
    assert valor.startswith("2026-11-15T11:00:00")
    assert json.loads(json.dumps(valor)) == valor  # já serializável


def test_bytes_viram_base64():
    assert backup._jsonable(b"\x00\x01") == {"__base64__": "AAE="}


def test_verify_aprova_backup_integro(tmp_path, capsys):
    path = backup._dump(_engine_with_data(), tmp_path)
    assert backup._verify(path) == 0
    assert "[OK]" in capsys.readouterr().out


def test_verify_reprova_contagem_divergente(tmp_path, capsys):
    """Se o arquivo foi truncado, a meta deixa de bater com o conteúdo."""
    path = backup._dump(_engine_with_data(), tmp_path)

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["tables"]["transactions"] = []  # simula perda de linhas

    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh)

    assert backup._verify(path) == 1
    assert "[ERRO]" in capsys.readouterr().out


def test_verify_reprova_arquivo_inexistente(tmp_path):
    assert backup._verify(tmp_path / "nao-existe.json.gz") == 2


def test_tabela_ausente_e_registrada_como_menos_um(tmp_path):
    """Banco antigo sem alembic_version não deve quebrar o dump."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    path = backup._dump(engine, tmp_path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["meta"]["row_counts"]["alembic_version"] == -1


def test_url_sem_database_url_falha_com_mensagem_clara(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        backup._url()
    assert "DATABASE_URL" in str(exc.value)
