from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    # Contador de gerações de sessão. Incrementado no logout (e em futuras
    # trocas de senha): qualquer token emitido com valor anterior passa a ser
    # rejeitado em get_current_user, mesmo dentro da validade do `exp`.
    token_version = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # `passive_deletes=True`: ao excluir a conta, o banco aplica o ON DELETE
    # CASCADE da FK. Sem isso o SQLAlchemy tentaria `UPDATE transactions SET
    # user_id = NULL` antes do DELETE e esbarraria no NOT NULL.
    transactions = relationship(
        "Transaction",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),)

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    type = Column(String, nullable=False)  # "income" ou "expense"
    category = Column(String, nullable=False)
    date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # user_id configurado como não nulo
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner = relationship("User", back_populates="transactions")


class PasswordResetToken(Base):
    """Pedido de redefinição de senha.

    Guardamos apenas o **hash SHA-256** do token. Se o banco vazar, os tokens
    em claro não podem ser usados: o atacante teria só hashes de 256 bits
    (inviáveis de reverter). O token em claro existe apenas no e-mail enviado.

    Regras aplicadas na rota de redefinição: uso único (`used_at`), expiração
    curta (`expires_at`) e invalidação de sessões (`token_version` do usuário).
    """

    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 em hexadecimal (64 caracteres) — nunca o token em claro.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = relationship("User")
