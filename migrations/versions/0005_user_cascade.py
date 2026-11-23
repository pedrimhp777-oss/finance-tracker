"""Cascata ao excluir a conta: transações somem junto com o usuário.

Necessário para a exclusão de conta (`DELETE /auth/me`): sem `ON DELETE
CASCADE`, o banco recusaria apagar um usuário que ainda tem transações — ou,
pior, sobrariam linhas órfãs apontando para um `user_id` inexistente.

O nome da constraint NÃO é fixo: a versão anterior desta migração assumia
`fk_transaction_user` e quebrou em produção, onde a constraint se chama
`transactions_user_id_fkey` porque o banco nasceu de `create_all` (e foi
apenas carimbado com `alembic stamp 0001_initial`), não das migrações. Banco
novo, criado por `alembic upgrade head`, tem o outro nome. A migração descobre
o nome real no catálogo em vez de adivinhar.

Revision ID: 0005_user_cascade
Revisão anterior: 0004_password_reset
Data: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_user_cascade"
down_revision = "0004_password_reset"
branch_labels = None
depends_on = None

FK_NAME = "fk_transaction_user"

# Nome da FK de `transactions.user_id`, qualquer que seja.
# O `conkey` é a lista de colunas da constraint; comparamos com o attnum de
# `user_id` para não pegar uma FK futura de outra coluna.
_FK_DE_USER_ID = sa.text(
    """
    SELECT c.conname
    FROM pg_constraint c
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.conrelid = 'transactions'::regclass
      AND c.contype = 'f'
      AND a.attname = 'user_id'
    """
)


def _nome_da_fk_existente() -> str | None:
    return op.get_bind().execute(_FK_DE_USER_ID).scalar()


def _recriar_fk(ondelete: str | None) -> None:
    existente = _nome_da_fk_existente()
    if existente:
        # Identificador vindo do catálogo do próprio banco, não de entrada
        # externa; ainda assim entre aspas para nomes com maiúsculas.
        op.execute(f'ALTER TABLE transactions DROP CONSTRAINT "{existente}"')
    op.create_foreign_key(
        FK_NAME,
        "transactions",
        "users",
        ["user_id"],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _recriar_fk(ondelete="CASCADE")


def downgrade() -> None:
    _recriar_fk(ondelete=None)
