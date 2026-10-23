from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from enum import Enum

# Restringe os tipos permitidos estritamente a "income" ou "expense"
class TransactionType(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"


# SCHEMAS DE USU�RIO
class UserBase(BaseModel):
    email: EmailStr
    name: str

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    
    class Config:
        from_attributes = True


# SCHEMAS DE TRANSA��O
class TransactionBase(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    amount: float = Field(gt=0, le=9_999_999_999.99, allow_inf_nan=False)
    type: TransactionType  # Agora utiliza o Enum em vez de str gen�rico!
    category: str

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int
    date: datetime
    user_id: int

    class Config:
        from_attributes = True


# SCHEMA DO RESUMO
class SummaryResponse(BaseModel):
    total_income: float
    total_expense: float
    balance: float
