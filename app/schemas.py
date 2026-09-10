from pydantic import BaseModel, EmailStr
from datetime import datetime
from enum import Enum

# Restringe os tipos permitidos estritamente a "income" ou "expense"
class TransactionType(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"


# SCHEMAS DE USUÁRIO
class UserBase(BaseModel):
    email: EmailStr
    name: str

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    id: int
    
    class Config:
        from_attributes = True


# SCHEMAS DE TRANSAÇÃO
class TransactionBase(BaseModel):
    description: str
    amount: float
    type: TransactionType  # Agora utiliza o Enum em vez de str genérico!
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