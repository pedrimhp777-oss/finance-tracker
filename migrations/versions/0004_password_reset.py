"""Migração 0004: tabela de tokens de redefinição de senha.

Aditiva: cria apenas uma tabela nova. Nenhuma coluna existente é alterada e
nenhum dado é lido, movido ou apagado — segura para rodar em produção.

O token em claro NUNCA é gravado: `token_hash` guarda SHA-256 (64 hex). Se o
banco for comprometido, os tokens em claro (que só existem no e-mail enviado)
não podem ser reconstruídos a partir dele.

Revision ID: 0004_password_reset
Revises: 0003_token_version
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_password_reset"
down_revision = "0003_token_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_reset_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Busca por token (a rota de redefinição) e listagem por usuário
    # (invalidação dos tokens anteriores).
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
