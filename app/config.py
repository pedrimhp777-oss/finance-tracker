"""Configuração derivada de ambiente compartilhada pelos módulos da app.

Centraliza o que antes estava duplicado em `app/main.py` e `app/security.py`.
"""
import os

from dotenv import load_dotenv

# Carregado AQUI, e não em `main.py`: `app.config` é importado no topo do main
# (antes do `load_dotenv()` que ficava no fim do arquivo), então APP_ENV,
# PASSWORD_RESET_URL e o TTL vindos do `.env` local eram silenciosamente
# ignorados — o módulo já tinha lido o ambiente do processo.
load_dotenv()

# Padrão SEGURO (fail closed): sem APP_ENV o app se comporta como produção —
# /docs e /openapi.json fechados, cookie com `Secure`. Desenvolvimento passou a
# ser opt-in explícito (`APP_ENV=development`), e não o contrário.
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

# Ambientes locais/teste: docs abertos e cookies sem `Secure` (HTTP puro).
IS_DEV_ENV = APP_ENV in {"development", "dev", "local", "test"}

# --- IP do cliente atrás de proxy --------------------------------------
# Ligado: confia em CF-Connecting-IP / True-Client-IP / X-Forwarded-For, o que
# é correto na Render (atrás do edge da Cloudflare). Desligado (padrão): usa
# apenas o IP da conexão TCP, porque cabeçalho enviado pelo cliente é forjável
# e um X-Forwarded-For falso criaria uma identidade nova a cada requisição,
# anulando o rate limit por IP.
#   Render/Vercel: definir TRUST_PROXY_HEADERS=true no serviço.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
    "sim",
}

# --- Cookies de autenticação ------------------------------------------
# ⚠️ LIMITAÇÃO ARQUITETURAL CONHECIDA (leia antes de migrar o frontend):
# hoje o frontend (…vercel.app) e a API (…onrender.com) são domínios
# registráveis DIFERENTES. Nesse cenário o cookie é "de terceiros" e
# navegadores o bloqueiam (Safari/ITP e a eliminação de third-party cookies
# no Chrome). Por isso o modo cookie é oferecido como ALTERNATIVA — o header
# `Authorization: Bearer` continua plenamente suportado e é o que o frontend
# usa hoje.
# Para migrar de vez para cookie é necessário um domínio compartilhado
# (ex.: app.exemplo.com + api.exemplo.com), quando o cookie passa a ser
# first-party.
#
# Frontend (Vercel) e API (Render) são origens distintas, então o cookie
# precisa de SameSite=None — que os navegadores só aceitam junto com Secure.
# Em desenvolvimento (http://localhost) usamos Lax e sem Secure.
COOKIE_SECURE = not IS_DEV_ENV
COOKIE_SAMESITE = "lax" if IS_DEV_ENV else "none"

AUTH_COOKIE_NAME = "access_token"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# --- Recuperação de senha ---------------------------------------------
# Validade do link enviado por e-mail. Curto de propósito: reduz a janela de
# uso de um token que vazou da caixa de entrada.
PASSWORD_RESET_TTL_MINUTES = int(os.getenv("PASSWORD_RESET_TTL_MINUTES", "30"))
# Base do link enviado ao usuário (o frontend monta a tela em /reset-password).
# Sem barra final. Ex.: https://finance-tracker-front-pearl.vercel.app
PASSWORD_RESET_URL = (
    os.getenv("PASSWORD_RESET_URL", "").strip().rstrip("/")
    or os.getenv("FRONTEND_URL", "").strip().rstrip("/")
)
