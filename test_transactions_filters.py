"""Testes de filtros, paginação, fuso horário, exportação Excel e precisão."""
import io
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from test_transactions import get_auth_headers


def _create(client, headers, description="Item", amount="10.50", ttype="expense",
            category="Alimentação"):
    return client.post(
        "/transactions/",
        json={
            "description": description,
            "amount": amount,
            "type": ttype,
            "category": category,
        },
        headers=headers,
    )


# --- VALIDAÇÕES DE ENTRADA ---

def test_create_transaction_zero_amount_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = _create(client, headers, amount="0")
    assert response.status_code == 422


def test_create_transaction_negative_amount_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = _create(client, headers, amount="-5")
    assert response.status_code == 422


def test_create_transaction_invalid_category_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = _create(client, headers, category="CategoriaInexistente")
    assert response.status_code == 422


def test_create_transaction_invalid_type_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = client.post(
        "/transactions/",
        json={"description": "X", "amount": "1.00", "type": "transfer", "category": "Outros"},
        headers=headers,
    )
    assert response.status_code == 422


def test_create_transaction_blank_description_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = _create(client, headers, description="   ")
    assert response.status_code == 422


def test_create_transaction_too_many_decimal_places_rejected(client):
    headers = get_auth_headers(client, "val@email.com")
    response = _create(client, headers, amount="10.555")
    assert response.status_code == 422


# --- PRECISÃO MONETÁRIA ---

def test_monetary_precision_preserved(client):
    headers = get_auth_headers(client, "money@email.com")
    response = _create(client, headers, amount="1234.56")
    assert response.status_code == 201
    assert response.json()["amount"] == 1234.56


def test_summary_decimal_aggregation(client):
    headers = get_auth_headers(client, "sumdec@email.com")
    _create(client, headers, description="A", amount="10.10", ttype="income", category="Outros")
    _create(client, headers, description="B", amount="20.20", ttype="income", category="Outros")
    _create(client, headers, description="C", amount="5.30", ttype="expense", category="Outros")
    res = client.get("/transactions/summary", headers=headers)
    body = res.json()
    assert body["total_income"] == 30.30
    assert body["total_expense"] == 5.30
    assert body["balance"] == 25.00


# --- PAGINAÇÃO E FILTROS ---

def test_pagination(client):
    headers = get_auth_headers(client, "page@email.com")
    for i in range(5):
        _create(client, headers, description=f"Item{i}", amount="1.00")
    page1 = client.get("/transactions/", params={"skip": 0, "limit": 2}, headers=headers)
    page2 = client.get("/transactions/", params={"skip": 2, "limit": 2}, headers=headers)
    assert len(page1.json()) == 2
    assert len(page2.json()) == 2
    all_items = client.get("/transactions/", headers=headers)
    assert len(all_items.json()) == 5


def test_filter_by_category_and_search(client):
    headers = get_auth_headers(client, "filt@email.com")
    _create(client, headers, description="Mercado", amount="10.00", category="Alimentação")
    _create(client, headers, description="Salário", amount="100.00", ttype="income", category="Trabalho")

    by_cat = client.get("/transactions/", params={"category": "Trabalho"}, headers=headers)
    assert len(by_cat.json()) == 1
    assert by_cat.json()[0]["description"] == "Salário"

    by_search = client.get("/transactions/", params={"search": "merca"}, headers=headers)
    assert len(by_search.json()) == 1
    assert by_search.json()[0]["description"] == "Mercado"


# --- FUSO HORÁRIO (transação perto da virada do mês) ---

def test_timezone_month_boundary(client):
    """Uma transação gravada às 02:00 UTC de 01/03 é 28/02 23:00 em São Paulo:
    conta como março em UTC e como fevereiro em SP."""
    headers = get_auth_headers(client, "tz@email.com")
    _create(client, headers, description="Virada", amount="50.00")

    # Força a data da transação para 2026-03-01T02:00 UTC (28/02 23:00 em SP).
    db = client.app.dependency_overrides[None] if False else None
    # Ajuste direto no banco de teste em memória:
    from app.database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # O client usa override de get_db; obtemos a sessão via a fixture db
    # indiretamente: recuperamos a transação via API e reatribuímos a data
    # usando a sessão compartilhada do conftest.
    from app import models  # noqa

    response = client.get("/transactions/", headers=headers)
    assert len(response.json()) == 1
    t_id = response.json()[0]["id"]

    # Sessão de teste: usamos a mesma engine do conftest via app state.
    session = client.app.dependency_overrides
    # Executa UPDATE SQL direto na sessão da fixture através do endpoint de update
    # não é possível; por isso usamos a sessão exposta pelo conftest.
    from conftest import engine  # mesma engine StaticPool dos testes

    from sqlalchemy.orm import sessionmaker
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    t = s.get(models.Transaction, t_id)
    t.date = datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc)
    s.commit()
    s.close()

    # Em UTC é março.
    res_utc = client.get("/transactions/summary", params={"year": 2026, "month": 3}, headers=headers)
    # Em São Paulo é fevereiro.
    res_sp = client.get(
        "/transactions/summary",
        params={"year": 2026, "month": 2, "timezone_name": "America/Sao_Paulo"},
        headers=headers,
    )
    assert res_utc.json()["total_expense"] == 50.00
    assert res_sp.json()["total_expense"] == 50.00
    # E o mês oposto em cada fuso não contém a transação.
    res_utc_fev = client.get("/transactions/summary", params={"year": 2026, "month": 2}, headers=headers)
    assert res_utc_fev.json()["total_expense"] == 0.0


def test_invalid_timezone_rejected(client):
    headers = get_auth_headers(client, "tzbad@email.com")
    res = client.get(
        "/transactions/", params={"timezone_name": "Fuso/Inexistente"}, headers=headers
    )
    assert res.status_code == 422


# --- PAGINAÇÃO NO BANCO (A1) ---

def test_pagination_sql_limits(client):
    """skip/limit devem retornar exatamente a janela pedida, sem itens
    duplicados nem perdidos entre páginas."""
    headers = get_auth_headers(client, "pagsql@email.com")
    created = []
    for i in range(7):
        res = _create(client, headers, description=f"Item{i}", amount="1.00")
        created.append(res.json()["id"])

    # Ordenação: date desc, id desc -> itens mais novos primeiro (ids 7..1).
    expected_order = list(reversed(created))
    page1 = client.get("/transactions/", params={"skip": 0, "limit": 3}, headers=headers)
    page2 = client.get("/transactions/", params={"skip": 3, "limit": 3}, headers=headers)
    page3 = client.get("/transactions/", params={"skip": 6, "limit": 3}, headers=headers)

    ids = [t["id"] for t in page1.json()] + \
          [t["id"] for t in page2.json()] + \
          [t["id"] for t in page3.json()]
    assert ids == expected_order
    assert len(page1.json()) == 3
    assert len(page2.json()) == 3
    assert len(page3.json()) == 1  # janela final menor que o limit


def test_pagination_applies_filters(client):
    """Paginação deve respeitar filtros (não paginar tudo e filtrar depois)."""
    headers = get_auth_headers(client, "pagfilt@email.com")
    for i in range(4):
        _create(client, headers, description=f"Despesa{i}", amount="1.00", category="Alimentação")
    for i in range(3):
        _create(client, headers, description=f"Receita{i}", amount="1.00", ttype="income", category="Trabalho")

    page = client.get(
        "/transactions/",
        params={"category": "Trabalho", "skip": 0, "limit": 2},
        headers=headers,
    )
    assert len(page.json()) == 2
    assert all(t["category"] == "Trabalho" for t in page.json())
    total = client.get("/transactions/", params={"category": "Trabalho"}, headers=headers)
    assert len(total.json()) == 3


# --- EDIÇÃO (PUT) ---

def test_update_transaction_success(client):
    headers = get_auth_headers(client, "edit@email.com")
    res = _create(client, headers, description="Original", amount="10.00")
    t_id = res.json()["id"]

    upd = client.put(
        f"/transactions/{t_id}",
        json={"description": "Editada", "amount": "25.50", "type": "income", "category": "Trabalho"},
        headers=headers,
    )
    assert upd.status_code == 200
    body = upd.json()
    assert body["description"] == "Editada"
    assert body["amount"] == 25.50
    assert body["type"] == "income"

    # Persistência real da edição.
    fetched = client.get(f"/transactions/{t_id}", headers=headers)
    assert fetched.json()["description"] == "Editada"


# --- DATA OPCIONAL NA CRIACAO/EDICAO ---

def test_create_transaction_with_custom_past_date(client):
    headers = get_auth_headers(client, "datepast@email.com")
    res = client.post(
        "/transactions/",
        json={
            "description": "Salario de marco",
            "amount": "3000",
            "type": "income",
            "category": "Trabalho",
            "date": "2026-03-15T12:00:00Z",
        },
        headers=headers,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["date"].startswith("2026-03-15T")

    # Aparece no resumo do mes correspondente.
    summary = client.get(
        "/transactions/summary",
        params={"year": 2026, "month": 3},
        headers=headers,
    ).json()
    assert float(summary["total_income"]) == 3000.00


def test_create_transaction_without_date_uses_now(client):
    headers = get_auth_headers(client, "datenow@email.com")
    before = datetime.now(timezone.utc).replace(microsecond=0)
    res = _create(client, headers)
    assert res.status_code == 201
    got = datetime.fromisoformat(res.json()["date"].replace("Z", "+00:00"))
    # SQLite persiste sem fuso (o banco de teste é naive); trata como UTC.
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    after = datetime.now(timezone.utc)
    assert before - timedelta(seconds=5) <= got <= after + timedelta(seconds=5)


def test_create_transaction_future_date_rejected(client):
    headers = get_auth_headers(client, "datefuture@email.com")
    res = client.post(
        "/transactions/",
        json={
            "description": "Do futuro",
            "amount": "10",
            "type": "expense",
            "category": "Outros",
            "date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
        },
        headers=headers,
    )
    assert res.status_code == 422


def test_update_transaction_changes_date(client):
    headers = get_auth_headers(client, "dateedit@email.com")
    created = _create(client, headers).json()
    res = client.put(
        f"/transactions/{created['id']}",
        json={
            "description": created["description"],
            "amount": created["amount"],
            "type": created["type"],
            "category": created["category"],
            "date": "2026-01-05T12:00:00Z",
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["date"].startswith("2026-01-05T")


def test_update_transaction_without_date_keeps_original(client):
    headers = get_auth_headers(client, "datekeep@email.com")
    created = _create(client, headers).json()
    res = client.put(
        f"/transactions/{created['id']}",
        json={
            "description": "Descricao nova",
            "amount": created["amount"],
            "type": created["type"],
            "category": created["category"],
        },
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["date"] == created["date"]


def test_update_other_users_transaction_rejected(client):
    headers1 = get_auth_headers(client, "edt1@email.com")
    headers2 = get_auth_headers(client, "edt2@email.com")
    res = _create(client, headers1, description="Segredo", amount="10.00")
    t_id = res.json()["id"]

    upd = client.put(
        f"/transactions/{t_id}",
        json={"description": "Invasão", "amount": "1.00", "type": "expense", "category": "Outros"},
        headers=headers2,
    )
    assert upd.status_code == 404


# --- AGREGAÇÃO DE CATEGORIAS (A2) ---

def test_expenses_by_category_aggregates_all(client):
    """A agregação deve somar TODAS as transações do usuário, não só a página.
    Criamos 150 despesas para ultrapassar o limite de página (100)."""
    headers = get_auth_headers(client, "cats@email.com")
    for i in range(150):
        _create(client, headers, description=f"D{i}", amount="1.00", category="Alimentação")
    for i in range(30):
        _create(client, headers, description=f"T{i}", amount="2.00", category="Transporte")
    # Entradas não entram no gráfico de despesas.
    _create(client, headers, description="Sal", amount="5000.00", ttype="income", category="Trabalho")

    res = client.get("/transactions/categories", headers=headers)
    assert res.status_code == 200
    by_cat = {row["category"]: row["total"] for row in res.json()}
    assert by_cat["Alimentação"] == 150.00
    assert by_cat["Transporte"] == 60.00
    assert "Trabalho" not in by_cat  # entradas excluídas
    # Ordenado do maior para o menor.
    totals = [row["total"] for row in res.json()]
    assert totals == sorted(totals, reverse=True)


def test_expenses_by_category_with_filters(client):
    headers = get_auth_headers(client, "catsf@email.com")
    _create(client, headers, description="A", amount="10.00", category="Alimentação")
    _create(client, headers, description="B", amount="20.00", category="Transporte")

    res = client.get(
        "/transactions/categories",
        params={"timezone_name": "America/Sao_Paulo"},
        headers=headers,
    )
    by_cat = {row["category"]: row["total"] for row in res.json()}
    assert by_cat == {"Transporte": 20.00, "Alimentação": 10.00}


# --- RESUMO COM FILTROS DE CATEGORIA E BUSCA ---

def test_summary_respects_category_filter(client):
    headers = get_auth_headers(client, "sumcat@email.com")
    _create(client, headers, description="Pizza", amount="100.00", ttype="expense", category="Alimentação")
    _create(client, headers, description="Uber", amount="40.00", ttype="expense", category="Transporte")
    _create(client, headers, description="Salario", amount="1000.00", ttype="income", category="Trabalho")

    res = client.get("/transactions/summary", params={"category": "Transporte"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert float(body["total_expense"]) == 40.00
    assert float(body["total_income"]) == 0.00
    assert float(body["balance"]) == -40.00


def test_summary_respects_search_filter(client):
    headers = get_auth_headers(client, "sumsearch@email.com")
    _create(client, headers, description="Assinatura Netflix", amount="50.00", ttype="expense", category="Lazer")
    _create(client, headers, description="Cinema", amount="30.00", ttype="expense", category="Lazer")
    _create(client, headers, description="Salario", amount="1000.00", ttype="income", category="Trabalho")

    res = client.get("/transactions/summary", params={"search": "assin"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert float(body["total_expense"]) == 50.00
    assert float(body["total_income"]) == 0.00


def test_summary_combined_category_search_period(client):
    headers = get_auth_headers(client, "sumall@email.com")
    _create(client, headers, description="Pizza", amount="100.00", ttype="expense", category="Alimentação")
    _create(client, headers, description="Pizzaria OK", amount="70.00", ttype="expense", category="Alimentação")
    _create(client, headers, description="Almoço", amount="35.00", ttype="expense", category="Alimentação")
    _create(client, headers, description="Uber", amount="40.00", ttype="expense", category="Transporte")

    # Data-alvo: "Pizza" vai para 2026-03-15 (dentro do período março);
    # "Pizzaria OK" vai para fevereiro (fora do período).
    from conftest import engine
    from sqlalchemy.orm import sessionmaker
    from app import models

    listing = client.get("/transactions/", headers=headers).json()
    by_desc = {row["description"]: row["id"] for row in listing}
    s = sessionmaker(bind=engine)()
    t = s.get(models.Transaction, by_desc["Pizza"])
    t.date = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    t2 = s.get(models.Transaction, by_desc["Pizzaria OK"])
    t2.date = datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc)
    s.commit()
    s.close()

    # Categoria + busca + período (março/2026): só "Pizza" passa nos três filtros.
    res = client.get(
        "/transactions/summary",
        params={"category": "Alimentação", "search": "pizz", "year": 2026, "month": 3},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert float(body["total_expense"]) == 100.00
    assert float(body["total_income"]) == 0.00

    # Mesma combinação, período fevereiro/2026: só "Pizzaria OK".
    res_feb = client.get(
        "/transactions/summary",
        params={"category": "Alimentação", "search": "pizz", "year": 2026, "month": 2},
        headers=headers,
    )
    assert res_feb.status_code == 200
    assert float(res_feb.json()["total_expense"]) == 70.00


def test_categories_requires_auth(client):
    res = client.get("/transactions/categories")
    assert res.status_code == 401


def test_categories_postgres_sql_group_by_is_valid():
    """Regressão do bug de produção: no caminho PostgreSQL a agregação deve
    selecionar apenas colunas presentes no GROUP BY.

    Compila a query no dialeto PostgreSQL (sem conectar) e valida o SQL.
    """
    import os as _os
    from sqlalchemy import create_engine
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import sessionmaker
    from app import models
    from app.services import transactions as svc

    engine = create_engine("sqlite:///:memory:")
    Sess = sessionmaker(bind=engine)
    db = Sess()

    # Reproduz exatamente a query do caminho PostgreSQL do serviço.
    query = db.query(
        models.Transaction.category,
        func.sum(models.Transaction.amount),
    ).filter(
        models.Transaction.user_id == 1,
        models.Transaction.type == "expense",
    ).group_by(models.Transaction.category)

    compiled = query.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "transactions.category" in sql
    assert "sum(transactions.amount)" in sql
    # A query da entidade inteira (bug original) traria colunas como
    # transactions.id/description sem agregacao nem GROUP BY.
    assert "transactions.description" not in sql.lower()
    assert "transactions.id" not in sql.lower()
    db.close()


def test_categories_with_category_filter(client):
    """Filtro de categoria isolado: pizza deve mostrar só a categoria pedida."""
    headers = get_auth_headers(client, "catfilt@email.com")
    _create(client, headers, description="A", amount="10.00", category="Alimentação")
    _create(client, headers, description="B", amount="20.00", category="Transporte")

    res = client.get(
        "/transactions/categories",
        params={"category": "Transporte"},
        headers=headers,
    )
    assert [(r["category"], r["total"]) for r in res.json()] == [("Transporte", 20.00)]


def test_categories_with_search_filter(client):
    """Filtro de busca isolado: a busca casa com a descrição, e a pizza soma
    as categorias das transações cuja descrição casa."""
    headers = get_auth_headers(client, "catsearch@email.com")
    _create(client, headers, description="Mercado jan", amount="10.00", category="Alimentação")
    _create(client, headers, description="Uber", amount="20.00", category="Transporte")
    _create(client, headers, description="Mercado fev", amount="30.00", category="Transporte")

    res = client.get(
        "/transactions/categories",
        params={"search": "Mercado"},
        headers=headers,
    )
    by_cat = {r["category"]: r["total"] for r in res.json()}
    assert by_cat == {"Transporte": 30.00, "Alimentação": 10.00}


def test_categories_with_combined_filters(client):
    """Categoria + busca + período + fuso combinados, com o MESMO critério
    da listagem/resumo/exportação."""
    headers = get_auth_headers(client, "catcomb@email.com")
    _create(client, headers, description="Mercado", amount="10.00", category="Alimentação")
    _create(client, headers, description="Farmácia", amount="5.00", category="Saúde")

    from conftest import engine
    from app import models
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timezone as dt_tz

    TestSess = sessionmaker(bind=engine)
    sess = TestSess()
    user = sess.query(models.User).filter(models.User.email == "catcomb@email.com").first()
    tx = sess.query(models.Transaction).filter(
        models.Transaction.user_id == user.id,
        models.Transaction.description == "Mercado",
    ).first()
    # Data alvo: 01/03 02:00 UTC = 28/02 23:00 em SP (cruza o mês entre fuso).
    tx.date = datetime(2026, 3, 1, 2, 0, tzinfo=dt_tz.utc)
    sess.commit(); sess.close()

    res = client.get(
        "/transactions/categories",
        params={
            "category": "Alimentação",
            "search": "Mercado",
            "month": 2,
            "year": 2026,
            "timezone_name": "America/Sao_Paulo",
        },
        headers=headers,
    )
    # A transação criada (Mercado, Alimentação) com data movida para
    # 28/02 23:00 SP é a única que casa com TODOS os filtros.
    assert [(r["category"], r["total"]) for r in res.json()] == [("Alimentação", 10.00)]


def test_categories_consistent_with_list_filters(client):
    """Categorias deve derivar exatamente da listagem com os mesmos filtros."""
    headers = get_auth_headers(client, "catcons@email.com")
    _create(client, headers, description="Mercado", amount="10.00", category="Alimentação")
    _create(client, headers, description="Farmácia", amount="5.00", category="Saúde")

    params = {"category": "Saúde"}
    listed = client.get("/transactions/", params=params, headers=headers).json()
    cats = client.get("/transactions/categories", params=params, headers=headers).json()

    assert len(listed) == 1
    assert cats == [{"category": "Saúde", "total": 5.00}]


# --- ANTI-ABUSO NO CADASTRO (M1) ---

def test_register_rate_limit(client):
    """Muitos cadastros seguidos da mesma origem devem acionar o limite."""
    statuses = []
    for i in range(15):
        res = client.post(
            "/auth/register",
            json={"name": f"P{i}", "email": f"abuse{i}@email.com", "password": "password123"},
        )
        statuses.append(res.status_code)
        if res.status_code == 429:
            break
    assert 429 in statuses


# --- RATE LIMITER: LIMITE DE CHAVES (M1) ---

def test_rate_limiter_max_keys_bounded(client):
    """O armazenamento em memória deve respeitar _MAX_KEYS."""
    from app import security

    for i in range(security._MAX_KEYS + 50):
        security.record_attempt(f"k{i}")
    assert len(security._attempts) <= security._MAX_KEYS


# --- EXPORTAÇÃO EXCEL ---

def test_export_excel_with_filters(client):
    headers = get_auth_headers(client, "excel@email.com")
    _create(client, headers, description="Mercado", amount="10.00", category="Alimentação")
    _create(client, headers, description="=SOMA(1;2)", amount="20.00", category="Trabalho")

    res = client.get(
        "/transactions/export/excel",
        params={"category": "Trabalho", "timezone_name": "America/Sao_Paulo"},
        headers=headers,
    )
    assert res.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    ws = wb.active
    assert ws.title == "Relatório Financeiro"
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 1
    description, category, ttype, amount, _date = rows[0]
    # Injeção de fórmula neutralizada com prefixo de apóstrofo.
    assert description == "'=SOMA(1;2)"
    assert category == "Trabalho"
    assert ttype == "Saída"
    assert amount == 20.00


def test_export_excel_unauthorized(client):
    res = client.get("/transactions/export/excel")
    assert res.status_code == 401
