"""Versão de token por usuário, habilitando revogação de sessão.

Revision ID: 0003_token_version
Revisão anterior: 0002_indexes
Data: 2026-11-15

Contexto: o logout só apagava o token no cliente; um token copiado continuava
válido até o `exp` (30 min). Com `token_version`, o logout incrementa o
contador do usuário e todos os tokens anteriores deixam de ser aceitos.

Migração ADITIVA e compatível com dados existentes: a coluna é criada com
`server_default='0'`, então as linhas atuais recebem 0 sem reescrita de dados.

ORDEM DE APLICAÇÃO (importante): aplicar esta migração **antes** de subir o
código que usa a coluna. O código novo consulta `users.token_version` em toda
requisição autenticada; se a coluna não existir, todas as rotas autenticadas
retornariam 500.
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_token_version"
down_revision = "0002_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
