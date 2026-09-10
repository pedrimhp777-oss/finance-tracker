from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def get_auth_headers():
    # Cadastra e loga um usuário exclusivo para o teste
    email = "test_finances@gmail.com"
    password = "password123"
    client.post("/auth/register", json={"name": "Pedro Teste", "email": email, "password": password})
    login_res = client.post("/auth/login", data={"username": email, "password": password})
    token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_create_transactions_and_verify_summary():
    headers = get_auth_headers()

    # 1. Cadastra uma receita (Entrada)
    client.post("/transactions/", headers=headers, json={
        "description": "Projeto Freelance",
        "amount": 2000.0,
        "type": "income",
        "category": "Trabalho"
    })

    # 2. Cadastra uma despesa (Saída)
    client.post("/transactions/", headers=headers, json={
        "description": "Compras da semana",
        "amount": 500.0,
        "type": "expense",
        "category": "Alimentação"
    })

    # 3. Valida se a API calcula o saldo correto (2000 - 500 = 1500)
    summary_res = client.get("/transactions/summary", headers=headers)
    assert summary_res.status_code == 200
    
    data = summary_res.json()
    assert data["total_income"] == 2000.0
    assert data["total_expense"] == 500.0
    assert data["balance"] == 1500.0