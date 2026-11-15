#!/usr/bin/env python
"""Backup lógico do banco — dump JSON comprimido de todas as tabelas.

Por que existe: o Instant Restore do Neon protege contra erro humano
dentro da janela de retenção (~6h no plano Free). Ele **não** protege
contra perder a conta, um projeto deletado ou um provedor indisponível.
Este script gera uma cópia independente, guardável em qualquer lugar.

Uso:

    # dump em ./backups/backup-AAAAMMDD-HHMMSS.json.gz
    python scripts/backup.py

    # diretório/arquivo específico
    python scripts/backup.py --out D:/backups

    # conferir integridade de um backup já gerado
    python scripts/backup.py --verify backups/backup-2026-11-15.json.gz

AVISO: o arquivo contém dados pessoais (e-mails e hashes bcrypt de senha).
   Trate como material sensível: guarde em local protegido, nunca no Git.

Este script apenas LÊ o banco. Nenhum comando de escrita é executado.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

# Tabelas que compõem o estado do sistema. A ordem importa para restauração
# manual: usuários antes das transações (chave estrangeira).
TABLES = ("users", "transactions", "alembic_version")

CHUNK = 500


def _url() -> str:
    """Lê DATABASE_URL e garante o driver psycopg (o app faz o mesmo)."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "DATABASE_URL não definida. Configure o .env antes de rodar."
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _jsonable(value):
    """Converte tipos do banco para algo serializável em JSON."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # str preserva a precisão exata (float perderia centavos).
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__base64__": base64.b64encode(bytes(value)).decode()}
    return str(value)


def _dump(engine, out_dir: Path) -> Path:
    started = datetime.now(timezone.utc)
    existing = set(inspect(engine).get_table_names())

    payload: dict = {}
    counts: dict[str, int] = {}

    with engine.connect() as conn:
        for table in TABLES:
            if table not in existing:
                counts[table] = -1  # ausente: schema anterior
                continue
            rows = []
            result = conn.execution_options(stream_results=True).execute(
                text(f"SELECT * FROM {table}")  # noqa: S608 - nome vem da allowlist
            )
            columns = list(result.keys())
            while True:
                chunk = result.fetchmany(CHUNK)
                if not chunk:
                    break
                for row in chunk:
                    rows.append(
                        {col: _jsonable(val) for col, val in zip(columns, row)}
                    )
            payload[table] = rows
            counts[table] = len(rows)

        alembic = None
        if "alembic_version" in existing:
            alembic = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()

    payload_doc = {
        "meta": {
            "generated_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "alembic_version": alembic,
            "tables": list(payload),
            "row_counts": counts,
            "format_version": 1,
        },
        "tables": payload,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"backup-{stamp}.json.gz"

    raw = json.dumps(payload_doc, ensure_ascii=False, indent=None).encode("utf-8")
    # mtime=0 mantém o gzip reprodutível (mesmo conteúdo -> mesmo hash).
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(path, "wb"), mtime=0) as gz:
        gz.write(raw)

    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _report(path: Path, meta: dict | None = None) -> None:
    size = path.stat().st_size
    print(f"\nBackup gravado: {path}")
    print(f"  tamanho:      {size / 1024:.1f} KB")
    print(f"  sha256:       {_sha256(path)}")
    if meta:
        print(f"  alembic:      {meta.get('alembic_version')}")
        print(f"  registros:    {meta.get('row_counts')}")
        print(f"  gerado em:    {meta.get('generated_at')}")
    print(
        "\n[AVISO]  Contém dados pessoais (e-mails e hashes de senha)."
        "\n    Guarde fora do Git e fora do provedor do banco."
    )


def _verify(path: Path) -> int:
    """Relê o arquivo e confere que o conteúdo é íntegro e legível."""
    if not path.exists():
        print(f"Arquivo não encontrado: {path}", file=sys.stderr)
        return 2
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)

    meta = doc.get("meta", {})
    tables = doc.get("tables", {})
    declarado = meta.get("row_counts", {})
    problemas = []

    for table, rows in tables.items():
        if declarado.get(table) != len(rows):
            problemas.append(
                f"{table}: meta diz {declarado.get(table)}, arquivo tem {len(rows)}"
            )
    if "users" not in tables:
        problemas.append("tabela 'users' ausente")
    if not meta.get("alembic_version"):
        problemas.append("alembic_version ausente na meta")

    _report(path, meta)
    if problemas:
        print("\n[ERRO] VERIFICAÇÃO FALHOU:")
        for p in problemas:
            print(f"   - {p}")
        return 1

    print("\n[OK] Backup íntegro: contagens conferem com a meta.")
    return 0


def main() -> int:
    # Console do Windows usa cp1252 por padrao; forca UTF-8 quando possivel.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    load_dotenv()

    parser = argparse.ArgumentParser(description="Backup lógico do Finance Tracker")
    parser.add_argument(
        "--out", default="backups", help="diretório de saída (padrão: ./backups)"
    )
    parser.add_argument(
        "--verify", metavar="ARQUIVO", help="apenas verifica um backup existente"
    )
    args = parser.parse_args()

    if args.verify:
        return _verify(Path(args.verify))

    # READ ONLY: nenhuma escrita é feita no banco.
    engine = create_engine(_url(), pool_pre_ping=True)
    path = _dump(engine, Path(args.out))
    engine.dispose()

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        meta = json.load(fh).get("meta", {})
    _report(path, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
