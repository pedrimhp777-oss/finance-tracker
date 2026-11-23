"""Mensagens de validação em português (F7).

O Pydantic responde em inglês e o texto ia direto para a tela. Estes testes
fixam duas coisas: a mensagem sai em português e o formato da resposta não
mudou (`detail` como lista, com `loc`, `msg`, `type`) — clientes existentes
continuam funcionando.
"""

import pytest

INSTRUCOES_EM_INGLES = (
    "should",
    "field required",
    "at least",
    "at most",
    "valid email",
    "valid string",
    "valid integer",
    "valid number",
    "greater than",
    "less than",
    "input should be",
    "value error",
)


def _mensagens(res) -> list[str]:
    assert res.status_code == 422, res.text
    return [item["msg"] for item in res.json()["detail"]]


def test_mensagem_nao_vaza_ingles():
    """Nenhuma das palavras do Pydantic pode chegar ao usuário."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    payloads = [
        {"name": "Ana", "email": "nao-e-email", "password": "123"},
        {"name": "", "email": "ana@email.com", "password": "12345678"},
        {"name": "Ana", "email": "ana@email.com", "password": ""},
        {"name": "x" * 200, "email": "ana@email.com", "password": "12345678"},
    ]

    for payload in payloads:
        for msg in _mensagens(client.post("/auth/register", json=payload)):
            baixa = msg.lower()
            for proibida in INSTRUCOES_EM_INGLES:
                assert proibida not in baixa, f"inglês vazou: {msg}"


def test_formato_continua_o_do_fastapi(client):
    """`detail` segue lista de objetos — só o texto de `msg` mudou."""
    res = client.post(
        "/auth/register",
        json={"name": "Ana", "email": "ana@email.com", "password": "123"},
    )

    assert res.status_code == 422
    item = res.json()["detail"][0]
    assert set(item) == {"loc", "msg", "type"}
    assert isinstance(item["loc"], list)
    assert item["type"] == "string_too_short"


def test_senha_curta(client):
    msgs = _mensagens(
        client.post(
            "/auth/register",
            json={"name": "Ana", "email": "ana@email.com", "password": "1234"},
        )
    )
    assert msgs == ["Senha: deve ter no mínimo 8 caracteres"]


def test_email_invalido(client):
    msgs = _mensagens(
        client.post(
            "/auth/register",
            json={"name": "Ana", "email": "nao-e-email", "password": "12345678"},
        )
    )
    assert msgs == ["E-mail: deve ser um e-mail válido"]


def test_campo_obrigatorio(client):
    msgs = _mensagens(
        client.post("/auth/register", json={"email": "ana@email.com", "password": "12345678"})
    )
    assert msgs == ["Nome: é obrigatório"]


def test_senha_acima_de_72_bytes_mantem_texto_do_validador(client):
    """O validador próprio já escreve em português; não deve ser sobrescrito."""
    msgs = _mensagens(
        client.post(
            "/auth/register",
            json={"name": "Ana", "email": "ana@email.com", "password": "á" * 37},
        )
    )
    assert msgs == ["Senha: A senha deve ter no máximo 72 bytes."]


def test_login_sem_formulario(client):
    """Login usa `OAuth2PasswordRequestForm`: `username` é o e-mail na tela."""
    msgs = _mensagens(client.post("/auth/login"))
    assert msgs == ["E-mail: é obrigatório", "Senha: é obrigatório"]


def test_valores_do_enum_em_portugues(client):
    """`type` é `income`/`expense` na API e "receita"/"despesa" na tela."""
    from test_transactions import get_auth_headers

    headers = get_auth_headers(client, "enum@email.com")
    res = client.post(
        "/transactions/",
        json={
            "description": "X",
            "amount": "10.00",
            "type": "doacao",
            "category": "Outros",
        },
        headers=headers,
    )

    assert _mensagens(res) == ["Tipo: deve ser um destes valores: receita ou despesa"]


def test_query_param_invalido(client):
    from test_transactions import get_auth_headers

    headers = get_auth_headers(client, "query@email.com")
    msgs = _mensagens(client.get("/transactions/", params={"month": 13}, headers=headers))

    assert msgs == ["Mês: deve ser menor ou igual a 12"]


def test_erro_sem_traducao_conhecida_nao_vaza_ingles():
    """Tipo novo do Pydantic cai num texto genérico, nunca no original."""
    from app.validation import _traduzir

    assert _traduzir({"type": "tipo_que_nao_existe", "msg": "Input should be a valid thing"}) == (
        "valor inválido"
    )


def test_contexto_ausente_nao_quebra():
    """`ctx` incompleto não pode virar 500 nem KeyError."""
    from app.validation import _traduzir

    assert _traduzir({"type": "string_too_short", "msg": "..."}) == "valor inválido"


@pytest.mark.parametrize(
    ("loc", "esperado"),
    [
        (("body", "password"), "Senha"),
        (("body", "new_password"), "Nova senha"),
        (("query", "month"), "Mês"),
        (("body", "items", 0, "amount"), "Valor"),
        ((), "Requisição"),
    ],
)
def test_rotulo_do_campo(loc, esperado):
    from app.validation import _rotulo

    assert _rotulo(loc) == esperado
