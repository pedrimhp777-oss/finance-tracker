from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_register_user():
    response = client.post(
        "/auth/register",
        json={
            "name": "Usuario Teste",
            "email": "teste_register@gmail.com",
            "password": "senha_teste_123"
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "teste_register@gmail.com"
    assert "id" in data

def test_login_user():
    # 1. Cadastra o usuário necessário para este teste específico
    client.post(
        "/auth/register",
        json={
            "name": "Usuario Login",
            "email": "teste_login@gmail.com",
            "password": "senha_teste_123"
        }
    )

    # 2. Executa a validação do login
    response = client.post(
        "/auth/login",
        data={
            "username": "teste_login@gmail.com",
            "password": "senha_teste_123"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"