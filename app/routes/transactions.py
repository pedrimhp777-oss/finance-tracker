from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import extract
from typing import List, Optional

from app.database import get_db
from app import models, schemas, security

router = APIRouter(prefix="/transactions", tags=["Transactions"])

# 1. CRIAR TRANSAÇÃO
@router.post("/", response_model=schemas.TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    transaction: schemas.TransactionCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    db_transaction = models.Transaction(
        description=transaction.description,
        amount=transaction.amount,
        type=transaction.type,
        category=transaction.category,
        user_id=current_user.id
    )
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


# 2. RESUMO FINANCEIRO (COM FILTRO OPCIONAL DE MÊS E ANO)
@router.get("/summary", response_model=schemas.SummaryResponse)
def get_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    query = db.query(models.Transaction).filter(models.Transaction.user_id == current_user.id)
    
    if month:
        query = query.filter(extract('month', models.Transaction.date) == month)
    if year:
        query = query.filter(extract('year', models.Transaction.date) == year)
        
    transactions = query.all()
    
    total_income = sum(t.amount for t in transactions if t.type == "income")
    total_expense = sum(t.amount for t in transactions if t.type == "expense")
    balance = total_income - total_expense
    
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance
    }


# 3. LISTAR TRANSAÇÕES (COM FILTRO OPCIONAL DE MÊS E ANO)
@router.get("/", response_model=List[schemas.TransactionResponse])
def list_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    query = db.query(models.Transaction).filter(models.Transaction.user_id == current_user.id)
    
    if month:
        query = query.filter(extract('month', models.Transaction.date) == month)
    if year:
        query = query.filter(extract('year', models.Transaction.date) == year)
        
    return query.all()


# 4. BUSCAR TRANSAÇÃO POR ID
@router.get("/{transaction_id}", response_model=schemas.TransactionResponse)
def get_transaction(
    transaction_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id
    ).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transação não encontrada")
    return db_transaction


# 5. ATUALIZAR TRANSAÇÃO
@router.put("/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction(
    transaction_id: int, 
    updated_data: schemas.TransactionCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id
    ).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transação não encontrada")
    
    db_transaction.description = updated_data.description
    db_transaction.amount = updated_data.amount
    db_transaction.type = updated_data.type
    db_transaction.category = updated_data.category
    
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


# 6. DELETAR TRANSAÇÃO
@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id
    ).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transação não encontrada")
    
    db.delete(db_transaction)
    db.commit()
    return None