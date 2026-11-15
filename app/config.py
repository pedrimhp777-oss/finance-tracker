"""Configuração derivada de ambiente compartilhada pelos módulos da app.

Centraliza o que antes estava duplicado em `app/main.py` e `app/security.py`.
"""
import os

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()

# Ambientes locais/teste: docs abertos e cookies sem `Secure` (HTTP puro).
IS_DEV_ENV = APP_ENV in {"development", "dev", "local", "test"}

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
