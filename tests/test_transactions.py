import pytest

def get_auth_headers(client, email="user@email.com", password="password123"):
    client.post("/auth/register", json={"name": "Pedro", "email": email, "password": password})
    res = client.post("/auth/login", data={"username": email, "password": password})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

# --- CAMINHO FELIZ ---

def test_create_transaction_success(client):
    headers = get_auth_headers(client, "create@email.com")
    response = client.post(
        "/transactions/",
        json={"description": "Salário", "amount": 3000.0, "type": "income", "category": "Trabalho"},
        headers=headers
    )
    assert response.status_code == 201
    assert response.json()["description"] == "Salário"
    assert response.json()["amount"] == 3000.0

def test_list_transactions_success(client):
    headers = get_auth_headers(client, "list@email.com")
    client.post(
        "/transactions/",
        json={"description": "Mercado", "amount": 150.0, "type": "expense", "category": "Alimentação"},
        headers=headers
    )
    response = client.get("/transactions/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1

def test_get_summary_success(client):
    headers = get_auth_headers(client, "summary@email.com")
    client.post("/transactions/", json={"description": "Freela", "amount": 1000.0, "type": "income", "category": "Trabalho"}, headers=headers)
    client.post("/transactions/", json={"description": "Luz", "amount": 200.0, "type": "expense", "category": "Moradia"}, headers=headers)
    
    response = client.get("/transactions/summary", headers=headers)
    assert response.status_code == 200
    assert response.json()["total_income"] == 1000.0
    assert response.json()["total_expense"] == 200.0
    assert response.json()["balance"] == 800.0

def test_delete_transaction_success(client):
    headers = get_auth_headers(client, "delete@email.com")
    res = client.post("/transactions/", json={"description": "Café", "amount": 10.0, "type": "expense", "category": "Outros"}, headers=headers)
    t_id = res.json()["id"]

    del_res = client.delete(f"/transactions/{t_id}", headers=headers)
    assert del_res.status_code == 204

# --- CASOS DE ERRO E ISOLAMENTO ---

def test_unauthorized_access(client):
    response = client.get("/transactions/")
    assert response.status_code == 401

def test_get_nonexistent_transaction(client):
    headers = get_auth_headers(client, "notfound@email.com")
    response = client.get("/transactions/9999", headers=headers)
    assert response.status_code == 404

def test_tenant_isolation(client):
    headers_user1 = get_auth_headers(client, "user1@email.com")
    headers_user2 = get_auth_headers(client, "user2@email.com")

    create_res = client.post(
        "/transactions/",
        json={"description": "Segredo", "amount": 500.0, "type": "income", "category": "Outros"},
        headers=headers_user1
    )
    t_id = create_res.json()["id"]

    # Usuário 2 tenta acessar e deletar a transação do Usuário 1
    assert client.get(f"/transactions/{t_id}", headers=headers_user2).status_code == 404
    assert client.delete(f"/transactions/{t_id}", headers=headers_user2).status_code == 404