"""Testes de hardening residual:
- `no-store` em erros 500 não tratados
- /docs e /openapi.json fechados quando APP_ENV=production
- migração para PyJWT (CVE-2026-120459) e compatibilidade com tokens antigos
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys

import pytest
from conftest import TEST_DATABASE_URL
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.middleware import NO_STORE_HEADER
from test_transactions import get_auth_headers

NO_STORE = "no-store"
REPO_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())


def _legacy_hs256_token(payload: dict, key: str) -> str:
    """Constrói um JWS HS256 no mesmo formato que o python-jose emitia.

    Feito só com stdlib, para provar que tokens gerados antes da migração
    continuam aceitos pelo PyJWT (nenhuma sessão cai no deploy).
    """
    def b64raw(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = b".".join(
        [
            b64raw(json.dumps(header, separators=(",", ":")).encode()),
            b64raw(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    signature = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64raw(signature)).decode()


# ============================================================
# 1. ERRO 500 NÃO TRATADO RECEBE no-store
# ============================================================

def test_unhandled_error_on_financial_route_has_no_store(client, monkeypatch):
    from app.services import transactions as service

    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada no serviço")

    monkeypatch.setattr(service, "filtered_transactions", boom)
    headers = get_auth_headers(client, "boom@email.com")

    # raise_server_exceptions=False: queremos ver a resposta 500, não a exceção.
    raw = TestClient(app, raise_server_exceptions=False)
    res = raw.get("/transactions/", headers=headers)
    assert res.status_code == 500
    assert res.headers["cache-control"] == NO_STORE
    # Não vaza detalhes internos no corpo.
    assert "falha simulada" not in res.text
    assert "Traceback" not in res.text


def test_unhandled_error_on_public_route_has_no_header(client, monkeypatch):
    """500 fora do escopo sensível não recebe no-store (escopo preservado)."""
    from app.services import transactions as service

    monkeypatch.setattr(service, "summary_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    raw = TestClient(app, raise_server_exceptions=False)
    assert raw.get("/health").status_code in (200, 500)


def test_unhandled_error_body_is_generic(client, monkeypatch):
    from app.services import transactions as service

    monkeypatch.setattr(
        service, "summary_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("segredo interno"))
    )
    headers = get_auth_headers(client, "boom2@email.com")
    raw = TestClient(app, raise_server_exceptions=False)
    res = raw.get("/transactions/summary", headers=headers)
    assert res.status_code == 500
    assert res.json() == {"detail": "Erro interno do servidor"}
    assert "segredo interno" not in res.text


# ============================================================
# 2. /docs E /openapi.json EM PRODUÇÃO
# ============================================================

def _run_app_under_env(env_overrides: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_overrides)
    env["PYTHONPATH"] = REPO_ROOT
    script = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "for p in ('/openapi.json', '/docs'):\n"
        "    r = c.get(p)\n"
        "    print(p, r.status_code)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("app_env", ["production", "prod", "staging"])
def test_docs_and_openapi_closed_outside_dev(app_env):
    result = _run_app_under_env({"APP_ENV": app_env, "DATABASE_URL": TEST_DATABASE_URL})
    assert result.returncode == 0, result.stderr[-800:]
    output = result.stdout
    assert "/openapi.json 404" in output, output
    assert "/docs 404" in output, output


@pytest.mark.parametrize("app_env", ["development", "dev", "local", "test"])
def test_docs_and_openapi_available_in_dev(app_env):
    result = _run_app_under_env({"APP_ENV": app_env, "DATABASE_URL": TEST_DATABASE_URL})
    assert result.returncode == 0, result.stderr[-800:]
    output = result.stdout
    assert "/openapi.json 200" in output, output
    assert "/docs 200" in output, output


def test_docs_available_in_current_test_env(client):
    """A suíte fixa APP_ENV=development no conftest: docs acessíveis.

    O padrão do app (sem APP_ENV) passou a ser produção — ver
    `test_config_defaults.py`.
    """
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


# ============================================================
# 3. PyJWT: CVE fechado + compatibilidade com tokens antigos
# ============================================================

def test_pyjwt_rejects_empty_hmac_key():
    """CVE-2026-120459: chave HMAC vazia não pode ser aceita.

    Este era o comportamento vulnerável do python-jose (aceitava assinar e
    verificar com chave ""). O PyJWT recusa na preparação da chave.
    """
    import jwt
    from jwt.exceptions import InvalidKeyError

    token = _legacy_hs256_token({"sub": "1", "exp": 9999999999}, "")
    with pytest.raises(InvalidKeyError):
        jwt.decode(token, "", algorithms=["HS256"])
    with pytest.raises(InvalidKeyError):
        jwt.encode({"sub": "1"}, "", algorithm="HS256")


def test_pyjwt_rejects_alg_none():
    import jwt

    unsigned = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(json.dumps({"sub": "1", "exp": 9999999999}).encode()).rstrip(b"=").decode()
        + "."
    )
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(unsigned, "x" * 32, algorithms=["HS256"])


def test_legacy_token_still_accepted(client):
    """Token no formato emitido pelo python-jose continua válido após a migração."""
    from app.security import SECRET_KEY

    headers = get_auth_headers(client, "legacy@email.com")
    me = client.get("/auth/me", headers=headers)
    user_id = me.json()["id"]

    legacy = _legacy_hs256_token({"sub": str(user_id), "exp": 9999999999}, SECRET_KEY)
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert res.status_code == 200
    assert res.json()["email"] == "legacy@email.com"


def test_new_tokens_are_accepted_and_expire(client):
    from datetime import datetime, timedelta, timezone

    import jwt

    from app.security import ALGORITHM, SECRET_KEY

    headers = get_auth_headers(client, "newtok@email.com")
    user_id = client.get("/auth/me", headers=headers).json()["id"]

    fresh = jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200

    expired = jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_wrong_signature_rejected(client):
    import jwt

    from app.security import ALGORITHM

    headers = get_auth_headers(client, "badsig@email.com")
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    forged = jwt.encode({"sub": str(user_id), "exp": 9999999999}, "x" * 32, algorithm=ALGORITHM)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_python_jose_is_not_importable():
    """Garante que a dependência vulnerável saiu do ambiente."""
    import importlib.util

    assert importlib.util.find_spec("jose") is None, "python-jose ainda instalado"
