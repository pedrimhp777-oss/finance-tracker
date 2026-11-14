"""Testes de filtros, paginação, fuso horário, exportação Excel e precisão."""
import io
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
