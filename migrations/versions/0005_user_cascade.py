"""Cascata ao excluir a conta: transações somem junto com o usuário.

Necessário para a exclusão de conta (`DELETE /auth/me`): sem `ON DELETE
CASCADE`, o banco recusaria apagar um usuário que ainda tem transações — ou,
pior, sobrariam linhas órfãs apontando para um `user_id` inexistente.

Revision ID: 0005_user_cascade
Revisão anterior: 0004_password_reset
Data: 2026-11-23
"""
from alembic import op


revision = "0005_user_cascade"
down_revision = "0004_password_reset"
branch_labels = None
depends_on = None

FK_NAME = "fk_transaction_user"


def upgrade() -> None:
    op.drop_constraint(FK_NAME, "transactions", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "transactions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "transactions", type_="foreignkey")
    op.create_foreign_key(FK_NAME, "transactions", "users", ["user_id"], ["id"])
