"""Estrutura inicial: users e transactions.

Revision ID: 0001_initial
Revisão base: -
Data: 2026-11-14

IMPORTANTE: bancos que JÁ possuem as tabelas (ex.: produção atual, criada via
create_all) não devem executar esta migração; basta marcá-la como aplicada:

    alembic stamp head

Ambientes novos executam `alembic upgrade head` normalmente.
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"])
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column(
            "date",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_transaction_user"),
        sa.CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
    )
    op.create_index(op.f("ix_transactions_id"), "transactions", ["id"])
    # Índices de desempenho ficam na migração 0002, para que a produção
    # (carimbada em 0001) possa aplicá-los com `alembic upgrade head`.


def downgrade() -> None:
    op.drop_index(op.f("ix_transactions_id"), table_name="transactions")
    op.drop_table("transactions")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
