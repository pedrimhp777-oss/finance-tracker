"""Tradução das mensagens de validação do Pydantic para português.

O Pydantic responde em inglês ("String should have at least 8 characters") e
esse texto ia direto para a tela: uma interface em português exibindo erro em
inglês para o usuário final. Traduzir no cliente seria frágil — a mensagem é
gerada no servidor, com `ctx` variável (min_length, gt, max_digits).

O formato de resposta continua o do FastAPI (`detail` é uma lista de objetos
com `loc`, `msg`, `type` e `ctx`), então nenhum cliente precisa mudar: o
frontend já exibe `msg`.
"""

from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

# Rótulos dos campos que aparecem na interface. O dicionário também protege
# contra vazar nome interno de campo (`hashed_password`) em texto de tela.
CAMPOS: dict[str, str] = {
    "name": "Nome",
    "email": "E-mail",
    # Login usa OAuth2PasswordRequestForm: o campo `username` carrega o e-mail.
    "username": "E-mail",
    "password": "Senha",
    "new_password": "Nova senha",
    "token": "Token",
    "description": "Descrição",
    "amount": "Valor",
    "type": "Tipo",
    "category": "Categoria",
    "date": "Data",
    "month": "Mês",
    "year": "Ano",
    "day": "Dia",
    "search": "Busca",
    "skip": "Paginação",
    "limit": "Limite",
    "timezone": "Fuso horário",
}

# Mensagens por tipo de erro do Pydantic. O `{...}` é preenchido com `ctx`.
MENSAGENS: dict[str, str] = {
    "missing": "é obrigatório",
    "string_too_short": "deve ter no mínimo {min_length} caracteres",
    "string_too_long": "deve ter no máximo {max_length} caracteres",
    "string_type": "deve ser um texto",
    "int_type": "deve ser um número inteiro",
    "int_parsing": "deve ser um número inteiro",
    "float_type": "deve ser um número",
    "float_parsing": "deve ser um número",
    "decimal_type": "deve ser um número",
    "decimal_parsing": "deve ser um número",
    "greater_than": "deve ser maior que {gt}",
    "greater_than_equal": "deve ser maior ou igual a {ge}",
    "less_than": "deve ser menor que {lt}",
    "less_than_equal": "deve ser menor ou igual a {le}",
    "decimal_max_digits": "deve ter no máximo {max_digits} dígitos",
    "decimal_max_places": "deve ter no máximo {decimal_places} casas decimais",
    "bool_type": "deve ser verdadeiro ou falso",
    "date_type": "deve ser uma data válida",
    "datetime_type": "deve ser uma data e hora válidas",
    "enum": "deve ser um destes valores: {valores}",
    "value_error": "valor inválido",
}

# Erros que não têm tradução própria e não devem vazar em inglês.
PADRAO = "valor inválido"

# Valores internos de enum que aparecem na tela. `income`/`expense` são os
# identificadores da API; o usuário lê "receita"/"despesa".
VALORES_PT = {"income": "receita", "expense": "despesa"}


def _valores_do_enum(ctx: dict[str, Any]) -> str:
    """Lista de valores aceitos, com "or" traduzido e identificadores legíveis.

    O Pydantic monta `expected` como `"'income' or 'expense'"` — em inglês e
    com o nome interno do campo.
    """
    esperado = str(ctx.get("expected", "")).strip().replace(" or ", " ou ")
    for interno, rotulo in VALORES_PT.items():
        esperado = esperado.replace(f"'{interno}'", rotulo).replace(f'"{interno}"', rotulo)
    return esperado

# Prefixos que o Pydantic adiciona a mensagens de validador customizado.
PREFIXOS = ("Value error, ", "Assertion failed, ")


class ErroDeValidacao(Exception):
    """Marcador de tipo: nunca é levantado, só documenta o contrato."""


def _rotulo(loc: tuple[Any, ...]) -> str:
    """Último elemento de `loc` legível: `("body", "password")` → "Senha"."""
    for parte in reversed(loc):
        if isinstance(parte, str) and parte not in ("body", "query", "path", "header"):
            return CAMPOS.get(parte, parte)
        if isinstance(parte, int):  # item de lista
            continue
    return "Requisição"


def _traduzir(erro: dict[str, Any]) -> str:
    tipo = str(erro.get("type", ""))
    ctx = erro.get("ctx") or {}
    msg = str(erro.get("msg", ""))

    # `value_error` é o canal dos validadores customizados desta aplicação e
    # já trazem texto em português ("A senha deve ter no máximo 72 bytes.").
    if tipo == "value_error":
        for prefixo in PREFIXOS:
            if msg.startswith(prefixo):
                msg = msg[len(prefixo) :]
                break
        # E-mail inválido vem pelo mesmo tipo, mas em inglês.
        if "email" in msg.lower() or "not a valid" in msg.lower():
            return "deve ser um e-mail válido"
        return msg or PADRAO

    modelo = MENSAGENS.get(tipo)
    if modelo is None:
        return PADRAO
    if tipo == "enum":
        valores = _valores_do_enum(ctx)
        return f"deve ser um destes valores: {valores}" if valores else PADRAO
    try:
        return modelo.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return PADRAO


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Substitui o handler padrão do FastAPI, mantendo o formato da resposta.

    HTTP 422 e `detail` como lista: o que muda é só o texto de `msg`. `type` e
    `ctx` ficam intactos para quem programa contra a API.
    """
    erros = []
    for erro in exc.errors():
        erros.append(
            {
                "loc": list(erro.get("loc", ())),
                "msg": f"{_rotulo(tuple(erro.get('loc', ())))}: {_traduzir(erro)}",
                "type": erro.get("type", ""),
            }
        )
    return JSONResponse(status_code=422, content={"detail": erros})
