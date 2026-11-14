"""Serviço de consultas de transações.

Centraliza os filtros (mês/ano/categoria/busca), o tratamento de fuso horário
e a agregação do resumo financeiro, evitando duplicação entre as rotas de
listagem, resumo e exportação.

Estratégia de datas:
- Os instantes são armazenados em UTC (DateTime(timezone=True)).
- No PostgreSQL, a conversão para o fuso do usuário acontece no banco
  (func.timezone), garantindo filtros por mês/anno corretos perto da virada.
- Em outros dialetos (SQLite nos testes), o filtro por período é aplicado
  em Python após a busca.
"""
from datetime import timezone as dt_timezone
from decimal import Decimal

from sqlalchemy import extract, func
from sqlalchemy.orm import Query, Session
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import models


def resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    """Valida e resolve o fuso horário informado (padrão UTC)."""
    try:
        return ZoneInfo(timezone_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError("Fuso horário inválido")


def _in_selected_period(
    transaction: models.Transaction,
    client_timezone: ZoneInfo,
    month: int | None,
    year: int | None,
) -> bool:
    value = transaction.date
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    local_value = value.astimezone(client_timezone)
    return (
        (month is None or local_value.month == month)
        and (year is None or local_value.year == year)
    )


def filtered_transactions(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    category: str | None = None,
    search: str | None = None,
    timezone_name: str | None = None,
) -> list[models.Transaction]:
    """Busca as transações do usuário aplicando todos os filtros opcionais."""
    query: Query = db.query(models.Transaction).filter(
        models.Transaction.user_id == user_id
    )

    if category:
        query = query.filter(models.Transaction.category == category)
    if search:
        query = query.filter(models.Transaction.description.ilike(f"%{search.strip()}%"))

    query = query.order_by(
        models.Transaction.date.desc(), models.Transaction.id.desc()
    )

    if month is None and year is None:
        return query.all()

    if db.get_bind().dialect.name == "postgresql":
        # Converte os instantes para o fuso do usuário antes de filtrar.
        local_date = func.timezone(timezone_name or "UTC", models.Transaction.date)
        if month is not None:
            query = query.filter(extract("month", local_date) == month)
        if year is not None:
            query = query.filter(extract("year", local_date) == year)
        return query.all()

    client_timezone = resolve_timezone(timezone_name)
    return [
        t
        for t in query.all()
        if _in_selected_period(t, client_timezone, month, year)
    ]


def summary_for(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    timezone_name: str | None = None,
) -> dict[str, Decimal]:
    """Resumo financeiro com precisão Decimal, agregado no banco."""
    def total_by_type(transaction_type: str) -> Decimal:
        query = db.query(
            func.coalesce(func.sum(models.Transaction.amount), 0)
        ).filter(
            models.Transaction.user_id == user_id,
            models.Transaction.type == transaction_type,
        )
        if month is not None or year is not None:
            if db.get_bind().dialect.name == "postgresql":
                local_date = func.timezone(
                    timezone_name or "UTC", models.Transaction.date
                )
                if month is not None:
                    query = query.filter(extract("month", local_date) == month)
                if year is not None:
                    query = query.filter(extract("year", local_date) == year)
            else:
                client_timezone = resolve_timezone(timezone_name)
                transactions_in_period = [
                    t.id
                    for t in db.query(models.Transaction).filter(
                        models.Transaction.user_id == user_id,
                        models.Transaction.type == transaction_type,
                    )
                    if _in_selected_period(t, client_timezone, month, year)
                ]
                query = query.filter(
                    models.Transaction.id.in_(transactions_in_period or [0])
                )

        return query.scalar() or Decimal("0")

    total_income = total_by_type("income")
    total_expense = total_by_type("expense")
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
    }
