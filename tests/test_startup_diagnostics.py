"""O diagnóstico de boot precisa dizer a verdade sobre a configuração.

Se este log mentir, ele é pior que a ausência dele: alguém confere o log,
vê "true" e para de investigar um rate limit que não está funcionando.
"""

import logging

import pytest

from app import config
from app.startup_diagnostics import log_security_configuration


@pytest.fixture
def restaurar_flag():
    original = config.TRUST_PROXY_HEADERS
    yield
    config.TRUST_PROXY_HEADERS = original


def test_flags_desligadas_viram_warning(caplog, restaurar_flag):
    """O padrão inseguro tem de ser barulhento, não silencioso."""
    config.TRUST_PROXY_HEADERS = False

    with caplog.at_level(logging.INFO, logger="app.startup_diagnostics"):
        log_security_configuration()

    registros = [r for r in caplog.records if "TRUST_PROXY_HEADERS" in r.message]
    assert len(registros) == 1
    assert registros[0].levelno == logging.WARNING
    assert "desativa o rate limit por IP" in registros[0].message


def test_flag_ligada_aparece_como_info(caplog, restaurar_flag):
    config.TRUST_PROXY_HEADERS = True

    with caplog.at_level(logging.INFO, logger="app.startup_diagnostics"):
        log_security_configuration()

    registros = [r for r in caplog.records if "TRUST_PROXY_HEADERS" in r.message]
    assert len(registros) == 1
    assert registros[0].levelno == logging.INFO
    assert "true" in registros[0].message


def test_log_nao_vaza_configuracao_sensivel(caplog, restaurar_flag):
    """Diagnóstico registra estado de flags, nunca segredos."""
    import os

    config.TRUST_PROXY_HEADERS = False

    with caplog.at_level(logging.INFO, logger="app.startup_diagnostics"):
        log_security_configuration()

    texto = " ".join(r.message for r in caplog.records).lower()
    segredo = os.getenv("SECRET_KEY", "")
    if segredo:
        assert segredo.lower() not in texto
    # Senha do banco no log seria vazamento em texto claro.
    assert "postgres:" not in texto
    assert "@" not in texto


def test_main_emite_o_diagnostico_no_boot():
    """A função existir não basta: `main.py` precisa chamá-la.

    Sem esta prova, apagar a chamada deixaria os testes acima verdes para
    sempre — eles chamam a função direto.
    """
    import os
    import pathlib
    import subprocess
    import sys

    repo = pathlib.Path(__file__).parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)

    resultado = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging; logging.basicConfig(level=logging.INFO); import app.main",
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
    )

    assert resultado.returncode == 0, resultado.stderr[-800:]
    assert "TRUST_PROXY_HEADERS" in resultado.stderr
