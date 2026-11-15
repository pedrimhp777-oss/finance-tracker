"""Índices de desempenho para as consultas mais comuns.

Revision ID: 0002_indexes
Revisão anterior: 0001_initial
Data: 2026-11-14

Contexto: a produção atual (Neon) tem as tabelas criadas por create_all e
NÃO executará 0001 (fará `alembic stamp head`). Esta migração separada cria
apenas os índices, então pode ser aplicada com `alembic upgrade head`
normalmente tanto na produção carimbada quanto em ambientes novos.

Seguro para PostgreSQL e SQLite (CREATE INDEX puro, sem DDL destrutivo).
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Listagem: filtro por usuário + ordenação por data.
    op.create_index(
        "ix_transactions_user_date", "transactions", ["user_id", "date"]
    )
    # Resumo: soma por tipo de transação de cada usuário.
    op.create_index(
        "ix_transactions_user_type", "transactions", ["user_id", "type"]
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_user_type", table_name="transactions")
    op.drop_index("ix_transactions_user_date", table_name="transactions")
