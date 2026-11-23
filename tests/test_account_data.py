"""Direitos do usuário: anos disponíveis, exportação e exclusão de conta.

O que está em jogo:
- **exclusão** é irreversível e apaga dado financeiro: precisa de prova de
  posse (senha), precisa levar junto tudo que pertence ao usuário e não pode
  deixar resíduo em outras contas;
- **exportação** é portabilidade (LGPD art. 18) e não pode levar credencial
  interna (hash de senha) nem perder centavos;
- **anos disponíveis** alimentam o filtro do frontend, que antes derivava a
  lista das transações já carregadas (só a página atual).
"""
from datetime import datetime, timezone

import pytest

from app import models, security
from test_transactions import get_auth_headers


def _create(client, headers, description, amount, type_="expense", category="Outros", date=None):
    payload = {
        "description": description,
        "amount": amount,
        "type": type_,
        "category": category,
    }
    if date:
        payload["date"] = date
    res = client.post("/transactions/", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ============================================================
# 1. GET /transactions/years
# ============================================================

def test_years_lista_anos_do_mais_recente_ao_mais_antigo(client):
    headers = get_auth_headers(client, "anos@email.com")
    _create(client, headers, "A", "10.00", date="2023-03-10T15:00:00Z")
    _create(client, headers, "B", "10.00", date="2025-07-01T15:00:00Z")
    _create(client, headers, "C", "10.00", date="2025-08-01T15:00:00Z")

    res = client.get("/transactions/years", headers=headers)

    assert res.status_code == 200
    # Distinto (2025 aparece duas vezes no banco) e em ordem decrescente.
    assert res.json() == [2025, 2023]


def test_years_nao_inclui_anos_de_outro_usuario(client):
    outro = get_auth_headers(client, "outro@email.com")
    _create(client, outro, "De outro", "10.00", date="2019-01-05T15:00:00Z")

    meu = get_auth_headers(client, "meu@email.com")
    _create(client, meu, "Meu", "10.00", date="2024-01-05T15:00:00Z")

    assert client.get("/transactions/years", headers=meu).json() == [2024]


def test_years_vazio_para_conta_sem_transacoes(client):
    headers = get_auth_headers(client, "vazio@email.com")
    res = client.get("/transactions/years", headers=headers)
    assert res.status_code == 200
    assert res.json() == []


def test_years_respeita_o_fuso_do_cliente(client):
    """31/12 às 02:00 UTC ainda é 2023 em São Paulo (UTC-3)."""
    headers = get_auth_headers(client, "fuso@email.com")
    _create(client, headers, "Virada", "10.00", date="2024-01-01T02:00:00Z")

    utc = client.get("/transactions/years", headers=headers, params={"timezone_name": "UTC"})
    sp = client.get(
        "/transactions/years",
        headers=headers,
        params={"timezone_name": "America/Sao_Paulo"},
    )

    assert utc.json() == [2024]
    assert sp.json() == [2023]


def test_years_rejeita_fuso_invalido(client):
    headers = get_auth_headers(client, "fuso-ruim@email.com")
    res = client.get(
        "/transactions/years", headers=headers, params={"timezone_name": "Marte/Olympus"}
    )
    assert res.status_code == 422


def test_years_exige_autenticacao(client):
    assert client.get("/transactions/years").status_code == 401


# ============================================================
# 2. GET /auth/me/export
# ============================================================

def test_export_traz_conta_e_transacoes(client):
    headers = get_auth_headers(client, "export@email.com")
    _create(client, headers, "Salário", "5000.55", type_="income", category="Trabalho")
    _create(client, headers, "Mercado", "120.45")

    res = client.get("/auth/me/export", headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert body["user"]["email"] == "export@email.com"
    assert len(body["transactions"]) == 2
    assert body["summary"]["transactions"] == 2
    # Centavos preservados: string, não float.
    assert body["summary"]["total_income"] == "5000.55"
    assert body["summary"]["total_expense"] == "120.45"
    assert body["summary"]["balance"] == "4880.10"


def test_export_nao_inclui_hash_de_senha(client):
    headers = get_auth_headers(client, "sem-hash@email.com")
    body = client.get("/auth/me/export", headers=headers).json()

    serializado = str(body)
    assert "hashed_password" not in serializado
    assert "$2b$" not in serializado


def test_export_nao_e_cacheavel(client):
    headers = get_auth_headers(client, "no-store-export@email.com")
    res = client.get("/auth/me/export", headers=headers)

    assert res.headers["Cache-Control"] == "no-store"
    assert "attachment" in res.headers["Content-Disposition"]


def test_export_de_conta_sem_transacoes(client):
    headers = get_auth_headers(client, "export-vazio@email.com")
    body = client.get("/auth/me/export", headers=headers).json()

    assert body["transactions"] == []
    assert body["summary"]["transactions"] == 0
    assert body["summary"]["balance"] == "0"


# ============================================================
# 3. DELETE /auth/me
# ============================================================

def test_delete_exige_autenticacao(client):
    res = client.request("DELETE", "/auth/me", json={"password": "password123"})
    assert res.status_code == 401


def test_delete_com_senha_errada_nao_apaga_nada(client, db):
    headers = get_auth_headers(client, "senha-errada@email.com")
    _create(client, headers, "Fica", "10.00")

    res = client.request(
        "DELETE", "/auth/me", json={"password": "nao-e-esta"}, headers=headers
    )

    # 403, e não 401: 401 em rota autenticada o frontend trata como sessão
    # expirada e deslogaria o usuário no meio da operação.
    assert res.status_code == 403
    assert db.query(models.User).filter_by(email="senha-errada@email.com").count() == 1
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_delete_apaga_conta_transacoes_e_tokens(client, db):
    email = "apagar@email.com"
    headers = get_auth_headers(client, email)
    _create(client, headers, "Some", "10.00")
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    token_antigo = headers["Authorization"]

    res = client.request("DELETE", "/auth/me", json={"password": "password123"}, headers=headers)
    assert res.status_code == 204

    # Sumiu do banco, junto com tudo que era dele (cascata da migração 0005).
    assert db.query(models.User).filter_by(email=email).count() == 0
    assert db.query(models.Transaction).filter_by(user_id=user_id).count() == 0
    assert db.query(models.PasswordResetToken).filter_by(user_id=user_id).count() == 0

    # O token emitido antes deixa de valer, e o login não acha mais a conta.
    assert client.get("/auth/me", headers={"Authorization": token_antigo}).status_code == 401
    assert client.post(
        "/auth/login", data={"username": email, "password": "password123"}
    ).status_code == 401


def test_delete_nao_afeta_outra_conta(client, db):
    alvo = get_auth_headers(client, "alvo@email.com")
    _create(client, alvo, "Do alvo", "10.00")

    vizinho = get_auth_headers(client, "vizinho@email.com")
    _create(client, vizinho, "Do vizinho", "20.00")

    assert client.request(
        "DELETE", "/auth/me", json={"password": "password123"}, headers=alvo
    ).status_code == 204

    assert db.query(models.User).filter_by(email="vizinho@email.com").count() == 1
    assert client.get("/auth/me", headers=vizinho).status_code == 200
    assert len(client.get("/transactions/", headers=vizinho).json()) == 1


def test_delete_tem_rate_limit(client, monkeypatch):
    """Sem limite, o endpoint viraria um oráculo para testar senhas."""
    headers = get_auth_headers(client, "forca-delete@email.com")
    for _ in range(security._MAX_ATTEMPTS + 1):
        res = client.request(
            "DELETE", "/auth/me", json={"password": "errada"}, headers=headers
        )
        if res.status_code == 429:
            break
    assert res.status_code == 429


def test_delete_remove_tokens_de_redefinicao(client, db, monkeypatch):
    """Quem apaga a conta não pode continuar com um link de reset pendente."""
    email = "com-reset@email.com"
    headers = get_auth_headers(client, email)
    user = db.query(models.User).filter_by(email=email).first()
    db.add(
        models.PasswordResetToken(
            user_id=user.id,
            token_hash="a" * 64,
            expires_at=datetime.now(timezone.utc).replace(year=2030),
        )
    )
    db.commit()

    assert client.request(
        "DELETE", "/auth/me", json={"password": "password123"}, headers=headers
    ).status_code == 204

    assert db.query(models.PasswordResetToken).filter_by(user_id=user.id).count() == 0
