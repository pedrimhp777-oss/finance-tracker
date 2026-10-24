from datetime import datetime
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
    pass


class TransactionResponse(TransactionBase):
    id: int
    date: datetime
    user_id: int

    class Config:
        from_attributes = True


class SummaryResponse(BaseModel):
    total_income: float
    total_expense: float
    balance: float
