from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.security import get_current_user
from app.services import transactions as transactions_service
from app.services import excel_export

router = APIRouter(prefix="/transactions", tags=["Transactions"])


def _timezone_or_422(timezone_name: str | None) -> str | None:
    try:
        transactions_service.resolve_timezone(timezone_name)
    except ValueError:
        raise HTTPException(status_code=422, detail="Fuso horário inválido")
    return timezone_name


# 1. CRIAR TRANSAÇÃO
@router.post("/", response_model=schemas.TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db_transaction = models.Transaction(
        description=transaction.description,
        amount=transaction.amount,
        type=transaction.type,
        category=transaction.category,
        user_id=current_user.id,
    )
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


# 2. RESUMO FINANCEIRO (AGREGAÇÃO DIRETA EM SQL COM FILTROS OPCIONAIS)
@router.get("/summary", response_model=schemas.SummaryResponse)
def get_summary(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=1, le=9999),
    timezone_name: str = Query("UTC", max_length=64),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _timezone_or_422(timezone_name)
    totals = transactions_service.summary_for(
        db,
        current_user.id,
        month=month,
        year=year,
        timezone_name=timezone_name,
    )
    return totals


# 2b. DESPESAS POR CATEGORIA (agregação server-side sobre TODAS as
# transações filtradas, com os mesmos critérios do resumo e da exportação)
@router.get("/categories", response_model=List[schemas.CategorySummary])
def get_expenses_by_category(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=1, le=9999),
    timezone_name: str = Query("UTC", max_length=64),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _timezone_or_422(timezone_name)
    return transactions_service.expenses_by_category(
        db,
        current_user.id,
        month=month,
        year=year,
        timezone_name=timezone_name,
    )


# 3. LISTAR TRANSAÇÕES (COM PAGINAÇÃO E FILTROS OPCIONAIS)
@router.get("/", response_model=List[schemas.TransactionResponse])
def list_transactions(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=1, le=9999),
    category: Optional[str] = Query(None, min_length=1, max_length=100),
    search: Optional[str] = Query(None, max_length=240),
    timezone_name: str = Query("UTC", max_length=64),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _timezone_or_422(timezone_name)
    return transactions_service.filtered_transactions(
        db,
        current_user.id,
        month=month,
        year=year,
        category=category,
        search=search,
        timezone_name=timezone_name,
        skip=skip,
        limit=limit,
    )


# 4. BUSCAR TRANSAÇÃO POR ID
@router.get("/{transaction_id}", response_model=schemas.TransactionResponse)
def get_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id,
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
    current_user: models.User = Depends(get_current_user),
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id,
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
    current_user: models.User = Depends(get_current_user),
):
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id,
        models.Transaction.user_id == current_user.id,
    ).first()

    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transação não encontrada")

    db.delete(db_transaction)
    db.commit()
    return None


# 7. EXPORTAÇÃO EXCEL
@router.get("/export/excel")
def export_excel(
    month: int | None = Query(None, ge=1, le=12),
    year: int | None = Query(None, ge=1, le=9999),
    category: str | None = Query(None, min_length=1, max_length=100),
    search: str | None = Query(None, max_length=240),
    timezone_name: str = Query("UTC", max_length=64),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _timezone_or_422(timezone_name)
    user_transactions = transactions_service.filtered_transactions(
        db,
        current_user.id,
        month=month,
        year=year,
        category=category,
        search=search,
        timezone_name=timezone_name,
    )
    stream = excel_export.build_workbook(
        user_transactions,
        transactions_service.resolve_timezone(timezone_name),
    )
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=relatorio_financeiro.xlsx"},
    )
