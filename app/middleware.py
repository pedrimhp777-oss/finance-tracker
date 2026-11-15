"""Middlewares de segurança aplicados a toda a API.

Separado de `app/main.py` para manter o arquivo de composição da aplicação
enxuto e permitir testar o middleware isoladamente.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Respostas que carregam informações financeiras do usuário. Todas as rotas
# ficam sob este prefixo: listagem, consulta individual, resumo, categorias,
# exportação, atualização e exclusão.
SENSITIVE_PATH_PREFIXES: tuple[str, ...] = ("/transactions",)

# Rotas de autenticação protegidas por correspondência EXATA. Não usamos
# prefixo "/auth" aqui de propósito: um prefixo protegeria qualquer caminho
# que apenas comece com "/auth" (ex.: "/auth/whatever"), mascarando rotas
# futuras que deveriam ser tratadas de outra forma. Correspondência exata
# garante que só estas três rotas recebem o header.
#   - /auth/login    -> devolve token de acesso
#   - /auth/register -> devolve dados pessoais do cadastro
#   - /auth/me       -> devolve dados pessoais do usuário
#   - /auth/logout   -> ação de sessão (mesma família de proteção)
SENSITIVE_EXACT_PATHS: frozenset[str] = frozenset(
    {"/auth/login", "/auth/register", "/auth/me", "/auth/logout"}
)

NO_STORE_HEADER = "no-store"


def is_sensitive_path(path: str) -> bool:
    """Indica se o caminho deve receber `Cache-Control: no-store`.

    Combina dois critérios explícitos:
    - prefixo `/transactions` (família inteira de rotas financeiras);
    - correspondência exata com as rotas de autenticação listadas.
    """
    return path in SENSITIVE_EXACT_PATHS or path.startswith(SENSITIVE_PATH_PREFIXES)


class NoStoreMiddleware:
    """Força `Cache-Control: no-store` em respostas sensíveis.

    Cobre as rotas financeiras (`/transactions*`) e as rotas de autenticação
    que expõem token ou dados pessoais (`/auth/login`, `/auth/register`,
    `/auth/me`).

    Por que ASGI puro (e não `BaseHTTPMiddleware`): a exportação Excel é um
    `StreamingResponse`. Middlewares baseados em `BaseHTTPMiddleware`
    consomem/reenvelopam o corpo, o que pode corromper o download de
    arquivos grandes. Aqui apenas interceptamos o evento
    `http.response.start`, sem tocar no corpo.

    O header é aplicado antes do status ser conhecido para o cliente, logo
    também cobre as respostas de erro dessas rotas (401/404/409/422/500).

    Requisições `OPTIONS` (preflight CORS) são ignoradas: elas não contêm
    dados do usuário e adicionar `no-store` ali anularia o cache de
    preflight controlado por `Access-Control-Max-Age`.
    """

    def __init__(
        self,
        app: ASGIApp,
        path_prefixes: tuple[str, ...] = SENSITIVE_PATH_PREFIXES,
        exact_paths: frozenset[str] = SENSITIVE_EXACT_PATHS,
    ):
        self.app = app
        self.path_prefixes = path_prefixes
        self.exact_paths = exact_paths

    def _applies(self, path: str) -> bool:
        return path in self.exact_paths or path.startswith(self.path_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not self._applies(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = NO_STORE_HEADER
            await send(message)

        await self.app(scope, receive, send_with_no_store)


# --- Headers estáticos de segurança -----------------------------------------
# Aplicados a TODA resposta da API, inclusive erros 401/404/422 e o preflight.
# -----------------------------------------------------------------------------
NOSNIFF_HEADER = "nosniff"


class SecurityHeadersMiddleware:
    """Adiciona `X-Content-Type-Options: nosniff` a todas as respostas.

    Por que isso importa numa API JSON: sem `nosniff`, navegadores antigos
    podem "adivinhar" o tipo de um corpo e interpretar como HTML/script algo
    que foi servido como `application/json`. O header força o navegador a
    respeitar o `Content-Type` declarado.

    Implementado em ASGI puro pelo mesmo motivo do `NoStoreMiddleware`: não
    bufferiza o corpo, então a exportação Excel (`StreamingResponse`) continua
    íntegra.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_nosniff(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[
                    "X-Content-Type-Options"
                ] = NOSNIFF_HEADER
            await send(message)

        await self.app(scope, receive, send_with_nosniff)
