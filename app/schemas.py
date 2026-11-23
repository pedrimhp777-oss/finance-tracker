from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, field_validator


class TransactionType(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionCategory(str, Enum):
    FOOD = "Alimentação"
    TRANSPORT = "Transporte"
    HOUSING = "Moradia"
    HEALTH = "Saúde"
    EDUCATION = "Educação"
    LEISURE = "Lazer"
    WORK = "Trabalho"
    OTHER = "Outros"


class UserBase(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("O nome não pode ficar vazio.")
        return value


class UserCreate(UserBase):
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def limit_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("A senha deve ter no máximo 72 bytes.")
        return value


class ForgotPasswordRequest(BaseModel):
    """Pedido de recuperação. A resposta é sempre genérica (sem enumeração)."""

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def limit_password_bytes(cls, value: str) -> str:
        # Mesmo limite do cadastro: o bcrypt ignora bytes após o 72º.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("A senha deve ter no máximo 72 bytes.")
        return value


class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True


class TransactionBase(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    amount: Decimal = Field(gt=Decimal("0"), max_digits=12, decimal_places=2)
    type: TransactionType
    category: TransactionCategory

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A descrição não pode ficar vazia.")
        return value


class TransactionCreate(TransactionBase):
    """Payload de criação/edição. `date` é opcional: quando omitido, a
    transação recebe a data/hora atual; quando informado, permite registrar
    uma transação de um dia passado (ex.: salário de um mês anterior).
    """
    date: datetime | None = None

    @field_validator("date")
    @classmethod
    def reject_future_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        # Tolerância de 1 dia: acomoda fuso horário do cliente (UTC+13).
        if aware > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("A data da transação não pode estar no futuro.")
        return value


class TransactionResponse(TransactionBase):
    # Return JSON numbers for clients doing arithmetic with amounts.
    amount: float
    id: int
    date: datetime
    user_id: int

    class Config:
        from_attributes = True


class SummaryResponse(BaseModel):
    total_income: float
    total_expense: float
    balance: float


class CategorySummary(BaseModel):
    """Agregação de despesas por categoria (gráficos)."""

    category: str
    total: float


class DeleteAccountRequest(BaseModel):
    """Exclusão de conta exige a senha: um token vazado não basta.

    O token vive no `localStorage` e, em caso de XSS, poderia ser usado sem que
    o dono percebesse. Pedir a senha força prova de posse do fator que só o
    usuário conhece.
    """

    password: str = Field(min_length=1, max_length=200)
