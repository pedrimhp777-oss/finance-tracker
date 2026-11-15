"""Middlewares de segurança aplicados a toda a API.

Separado de `app/main.py` para manter o arquivo de composição da aplicação
enxuto e permitir testar o middleware isoladamente.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Prefixo das respostas que carregam informações financeiras do usuário.
# Inclui listagem, consulta individual, resumo, categorias, exportação,
# atualização e exclusão (todas sob /transactions).
SENSITIVE_PATH_PREFIXES: tuple[str, ...] = ("/transactions",)

NO_STORE_HEADER = "no-store"


class NoStoreMiddleware:
    """Força `Cache-Control: no-store` em respostas financeiras.

    Por que ASGI puro (e não `BaseHTTPMiddleware`): a exportação Excel é um
    `StreamingResponse`. Middlewares baseados em `BaseHTTPMiddleware`
    consomem/reenvelopam o corpo, o que pode corromper o download de
    arquivos grandes. Aqui apenas interceptamos o evento
    `http.response.start`, sem tocar no corpo.

    O header é aplicado por **prefixo de caminho**, portanto cobre
    automaticamente todos os endpoints de transações e também as respostas
    de erro dessas rotas (401/404/422/500), independentemente do status.

    Requisições `OPTIONS` (preflight CORS) são ignoradas: elas não contêm
    dados do usuário e adicionar `no-store` ali anularia o cache de
    preflight controlado por `Access-Control-Max-Age`.
    """

    def __init__(self, app: ASGIApp, path_prefixes: tuple[str, ...] = SENSITIVE_PATH_PREFIXES):
        self.app = app
        self.path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not scope.get("path", "").startswith(self.path_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = NO_STORE_HEADER
            await send(message)

        await self.app(scope, receive, send_with_no_store)
