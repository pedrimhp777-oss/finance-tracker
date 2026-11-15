"""Serviço de consultas de transações.

Centraliza os filtros (mês/ano/categoria/busca), o tratamento de fuso horário,
a paginação e as agregações, evitando duplicação entre listagem, resumo,
gráficos de categoria e exportação.

Estratégia de datas:
- Os instantes são armazenados em UTC (DateTime(timezone=True)).
- No PostgreSQL, a conversão para o fuso do usuário acontece no banco
  (func.timezone), garantindo filtros por mês/ano corretos perto da virada.
- Em outros dialetos (SQLite nos testes), o filtro por período é aplicado
  em Python após a busca e a paginação é aplicada em memória (volume pequeno).
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


def _base_query(
    db: Session,
    user_id: int,
    category: str | None,
    search: str | None,
) -> Query:
    query: Query = db.query(models.Transaction).filter(
        models.Transaction.user_id == user_id
    )
    if category:
        query = query.filter(models.Transaction.category == category)
    if search:
        query = query.filter(models.Transaction.description.ilike(f"%{search.strip()}%"))
    return query


def _period_in_sql(db: Session, query: Query, month: int | None, year: int | None, timezone_name: str | None) -> Query:
    """Aplica filtros de mês/ano no SQL (PostgreSQL), convertendo o fuso."""
    local_date = func.timezone(timezone_name or "UTC", models.Transaction.date)
    if month is not None:
        query = query.filter(extract("month", local_date) == month)
    if year is not None:
        query = query.filter(extract("year", local_date) == year)
    return query


def filtered_transactions(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    category: str | None = None,
    search: str | None = None,
    timezone_name: str | None = None,
    skip: int = 0,
    limit: int | None = None,
) -> list[models.Transaction]:
    """Busca transações do usuário com filtros opcionais.

    No PostgreSQL a paginação é feita no banco (OFFSET/LIMIT). Em dialetos sem
    suporte ao filtro por fuso (SQLite nos testes), o período é filtrado em
    Python e a paginação é aplicada sobre o resultado filtrado.
    """
    query = _base_query(db, user_id, category, search)
    query = query.order_by(
        models.Transaction.date.desc(), models.Transaction.id.desc()
    )

    has_period_filter = month is not None or year is not None

    if db.get_bind().dialect.name == "postgresql":
        if has_period_filter:
            query = _period_in_sql(db, query, month, year, timezone_name)
        if skip:
            query = query.offset(skip)
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    rows = query.all()
    if has_period_filter:
        client_timezone = resolve_timezone(timezone_name)
        rows = [t for t in rows if _in_selected_period(t, client_timezone, month, year)]
    if limit is None:
        return rows[skip:]
    return rows[skip : skip + limit]


def expenses_by_category(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    category: str | None = None,
    search: str | None = None,
    timezone_name: str | None = None,
) -> list[dict[str, object]]:
    """Agrega despesas por categoria, sobre TODAS as transações filtradas.

    Usa os mesmos critérios de período/resumo/exportação — incluindo os
    filtros de categoria e busca — para garantir consistência entre gráficos,
    cards e planilha.
    """
    has_period_filter = month is not None or year is not None

    if db.get_bind().dialect.name == "postgresql":
        # Agregação seleciona apenas colunas (categoria + soma). NÃO usar a
        # query de entidade aqui: SELECT de colunas não agrupadas com
        # GROUP BY é rejeitado pelo PostgreSQL (no SQLite não dá erro,
        # o que esconderia o bug nos testes).
        query = db.query(
            models.Transaction.category,
            func.sum(models.Transaction.amount),
        ).filter(
            models.Transaction.user_id == user_id,
            models.Transaction.type == "expense",
        )
        if category:
            query = query.filter(models.Transaction.category == category)
        if search:
            query = query.filter(models.Transaction.description.ilike(f"%{search.strip()}%"))
        if has_period_filter:
            local_date = func.timezone(timezone_name or "UTC", models.Transaction.date)
            if month is not None:
                query = query.filter(extract("month", local_date) == month)
            if year is not None:
                query = query.filter(extract("year", local_date) == year)
        rows = (
            query.group_by(models.Transaction.category)
            .order_by(func.sum(models.Transaction.amount).desc())
            .all()
        )
        return [{"category": c, "total": float(total)} for c, total in rows]

    rows = (
        _base_query(db, user_id, category, search)
        .filter(models.Transaction.type == "expense")
        .all()
    )
    if has_period_filter:
        client_timezone = resolve_timezone(timezone_name)
        rows = [t for t in rows if _in_selected_period(t, client_timezone, month, year)]

    totals: dict[str, Decimal] = {}
    for t in rows:
        totals[t.category] = totals.get(t.category, Decimal("0")) + t.amount
    return [
        {"category": category, "total": float(total)}
        for category, total in sorted(totals.items(), key=lambda kv: -kv[1])
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
                query = _period_in_sql(db, query, month, year, timezone_name)
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
