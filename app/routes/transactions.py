from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract, func
from typing import List, Optional

from app.database import get_db
from app import models, schemas, security

router = APIRouter(prefix="/transactions", tags=["Transactions"])

# 1. CRIAR TRANSA��O
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


# 2. RESUMO FINANCEIRO (AGREGA��O DIRETA EM SQL COM FILTROS OPCIONAIS)
@router.get("/summary", response_model=schemas.SummaryResponse)
def get_summary(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=1, le=9999),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    def get_total_by_type(transaction_type: str) -> float:
        query = db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0)).filter(
            models.Transaction.user_id == current_user.id,
            models.Transaction.type == transaction_type
        )
        if month:
            query = query.filter(extract('month', models.Transaction.date) == month)
        if year:
            query = query.filter(extract('year', models.Transaction.date) == year)
        return float(query.scalar())

    total_income = get_total_by_type("income")
    total_expense = get_total_by_type("expense")
    balance = total_income - total_expense

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance
    }


# 3. LISTAR TRANSA��ES (COM PAGINA��O E FILTROS OPCIONAIS)
@router.get("/", response_model=List[schemas.TransactionResponse])
def list_transactions(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=1, le=9999),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    query = db.query(models.Transaction).filter(models.Transaction.user_id == current_user.id)
    
    if month:
        query = query.filter(extract('month', models.Transaction.date) == month)
    if year:
        query = query.filter(extract('year', models.Transaction.date) == year)
        
    return query.order_by(models.Transaction.date.desc(), models.Transaction.id.desc()).offset(skip).limit(limit).all()


# 4. BUSCAR TRANSA��O POR ID
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
        raise HTTPException(status_code=404, detail="Transa��o n�o encontrada")
    return db_transaction


# 5. ATUALIZAR TRANSA��O
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
        raise HTTPException(status_code=404, detail="Transa��o n�o encontrada")
    
    db_transaction.description = updated_data.description
    db_transaction.amount = updated_data.amount
    db_transaction.type = updated_data.type
    db_transaction.category = updated_data.category
    
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


# 6. DELETAR TRANSA��O
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
        raise HTTPException(status_code=404, detail="Transa��o n�o encontrada")
    
    db.delete(db_transaction)
    db.commit()
    return None
