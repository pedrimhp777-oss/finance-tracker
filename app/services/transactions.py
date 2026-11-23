"""Serviço de consultas de transações.

Centraliza os filtros (mês/ano/categoria/busca), o tratamento de fuso horário,
a paginação e as agregações, evitando duplicação entre listagem, resumo,
gráficos de categoria e exportação.

Estratégia de datas:
- Os instantes são armazenados em UTC (DateTime(timezone=True)).
- A conversão para o fuso do usuário acontece no banco (func.timezone), o que
  mantém filtros por mês/ano corretos perto da virada do dia.

Um único caminho, PostgreSQL. Até o B1 existia um segundo caminho para SQLite
(filtro em Python + paginação em memória) porque a suíte rodava em memória;
além de dobrar o código, ele deixava o caminho de produção sem teste — foi
assim que um GROUP BY inválido escapou e quebrou o deploy.
"""
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


def _escape_like(term: str) -> str:
    """Escapa os curingas do LIKE.

    Sem isso, buscar por `%` casa com tudo e `_` casa com qualquer caractere:
    não é injeção de SQL (os parâmetros são vinculados), mas a busca devolve
    resultados que o usuário não pediu.
    """
    return (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
        query = query.filter(
            models.Transaction.description.ilike(
                f"%{_escape_like(search.strip())}%", escape="\\"
            )
        )
    return query


def _period_in_sql(
    db: Session,
    query: Query,
    month: int | None,
    year: int | None,
    timezone_name: str | None,
    day: int | None = None,
) -> Query:
    """Aplica filtros de mês/ano/dia no SQL (PostgreSQL), convertendo o fuso."""
    local_date = func.timezone(timezone_name or "UTC", models.Transaction.date)
    if month is not None:
        query = query.filter(extract("month", local_date) == month)
    if year is not None:
        query = query.filter(extract("year", local_date) == year)
    if day is not None:
        query = query.filter(extract("day", local_date) == day)
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
    day: int | None = None,
) -> list[models.Transaction]:
    """Busca transações do usuário com filtros opcionais.

    Filtro de período e paginação (OFFSET/LIMIT) acontecem no banco.
    """
    query = _base_query(db, user_id, category, search)
    query = query.order_by(
        models.Transaction.date.desc(), models.Transaction.id.desc()
    )

    if month is not None or year is not None or day is not None:
        query = _period_in_sql(db, query, month, year, timezone_name, day)
    if skip:
        query = query.offset(skip)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def expenses_by_category(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    category: str | None = None,
    search: str | None = None,
    timezone_name: str | None = None,
    day: int | None = None,
) -> list[dict[str, object]]:
    """Agrega despesas por categoria, sobre TODAS as transações filtradas.

    Usa os mesmos critérios de período/resumo/exportação — incluindo os
    filtros de categoria e busca — para garantir consistência entre gráficos,
    cards e planilha.
    """
    has_period_filter = month is not None or year is not None or day is not None

    # A agregação seleciona apenas colunas (categoria + soma). NÃO usar a query
    # de entidade aqui: SELECT de colunas não agrupadas junto com GROUP BY é
    # rejeitado pelo PostgreSQL. Foi exatamente esse o bug que só apareceu no
    # deploy, quando os testes ainda rodavam em SQLite (que aceita a query).
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
        query = query.filter(
            models.Transaction.description.ilike(
                f"%{_escape_like(search.strip())}%", escape="\\"
            )
        )
    if has_period_filter:
        query = _period_in_sql(db, query, month, year, timezone_name, day)
    rows = (
        query.group_by(models.Transaction.category)
        .order_by(func.sum(models.Transaction.amount).desc())
        .all()
    )
    return [{"category": c, "total": float(total)} for c, total in rows]


def summary_for(
    db: Session,
    user_id: int,
    month: int | None = None,
    year: int | None = None,
    timezone_name: str | None = None,
    category: str | None = None,
    search: str | None = None,
    day: int | None = None,
) -> dict[str, Decimal]:
    """Resumo financeiro com precisão Decimal, agregado no banco.

    Aceita os mesmos filtros da listagem (categoria, busca) além do
    período, para que os cards do dashboard reflitam o que está
    sendo filtrado.
    """
    def total_by_type(transaction_type: str) -> Decimal:
        query = db.query(
            func.coalesce(func.sum(models.Transaction.amount), 0)
        ).filter(
            models.Transaction.user_id == user_id,
            models.Transaction.type == transaction_type,
        )
        if category:
            query = query.filter(models.Transaction.category == category)
        if search:
            query = query.filter(
                models.Transaction.description.ilike(
                    f"%{_escape_like(search.strip())}%", escape="\\"
                )
            )
        if month is not None or year is not None or day is not None:
            query = _period_in_sql(db, query, month, year, timezone_name, day)

        return query.scalar() or Decimal("0")

    total_income = total_by_type("income")
    total_expense = total_by_type("expense")
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": total_income - total_expense,
    }


def available_years(
    db: Session,
    user_id: int,
    timezone_name: str | None = None,
) -> list[int]:
    """Anos em que o usuário tem ao menos uma transação, do mais recente ao
    mais antigo, já no fuso do cliente.

    Alimenta o filtro de ano do frontend. Derivar a lista das transações já
    carregadas limitava o seletor à página atual — e, com um ano selecionado,
    ao próprio ano escolhido, escondendo o histórico.
    """
    local_date = func.timezone(timezone_name or "UTC", models.Transaction.date)
    year_expression = extract("year", local_date)
    rows = (
        db.query(year_expression)
        .filter(models.Transaction.user_id == user_id)
        .distinct()
        .order_by(year_expression.desc())
        .all()
    )
    return [int(year) for (year,) in rows if year is not None]
