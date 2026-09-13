import pytest

# --- CAMINHO FELIZ ---

def test_register_user_success(client):
    response = client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "pedro@email.com", "password": "password123"}
    )
    assert response.status_code == 201
    assert response.json()["email"] == "pedro@email.com"
    assert "id" in response.json()

def test_login_success(client):
    client.post(
        "/auth/register",
        json={"name": "Pedro", "email": "login@email.com", "password": "password123"}
    )
    response = client.post(
        "/auth/login",
        data={"username": "login@email.com", "password": "password123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"

# --- CASOS DE ERRO E BORDA ---

def test_register_duplicate_email(client):
    user_data = {"name": "Pedro", "email": "duplicado@email.com", "password": "password123"}
    client.post("/auth/register", json=user_data)
    
    response = client.post("/auth/register", json=user_data)
    assert response.status_code == 400
    assert "já cadastrado" in response.json()["detail"].lower()

def test_login_wrong_password(client):
    client.post("/auth/register", json={"name": "Pedro", "email": "wrongpwd@email.com", "password": "correct_password"})
    
    response = client.post("/auth/login", data={"username": "wrongpwd@email.com", "password": "incorrect_password"})
    assert response.status_code == 401

def test_login_nonexistent_user(client):
    response = client.post("/auth/login", data={"username": "notfound@email.com", "password": "any_password"})
    assert response.status_code == 401