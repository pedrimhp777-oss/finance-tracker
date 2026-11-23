"""Testes do validador de força da SECRET_KEY.

Estes testes garantem que valores evidentemente fracos são rejeitados no
startup. Eles NÃO atestam entropia criptográfica — apenas as regras
estáticas implementadas em `app.security.validate_secret_key`.
"""
import secrets

import pytest
from conftest import TEST_DATABASE_URL

from app.security import (
    MIN_DISTINCT_CHARACTERS,
    MIN_SECRET_KEY_LENGTH,
    validate_secret_key,
)


def _assert_rejected(value, expected_fragment=""):
    with pytest.raises(RuntimeError) as exc:
        validate_secret_key(value)
    message = str(exc.value)
    if expected_fragment:
        assert expected_fragment in message
    # A mensagem nunca deve reproduzir a chave testada.
    if value and len(value) >= 8:
        assert value not in message
    return message


# --- CHAVE AUSENTE / VAZIA ---

def test_missing_key_is_rejected():
    _assert_rejected(None, "não pode ser vazia")


def test_empty_key_is_rejected():
    _assert_rejected("", "não pode ser vazia")


def test_whitespace_only_key_is_rejected():
    _assert_rejected(" " * 32, "apenas espaços")
    _assert_rejected("\t" * 32, "apenas espaços")
    _assert_rejected(" \n\t " * 10, "apenas espaços")


# --- COMPRIMENTO ---

def test_key_shorter_than_minimum_is_rejected():
    _assert_rejected("aB3" * 10, f"ao menos {MIN_SECRET_KEY_LENGTH} caracteres")


def test_key_one_char_below_minimum_is_rejected():
    # 31 caracteres distintos, ainda assim curta demais.
    value = "".join(chr(33 + i) for i in range(MIN_SECRET_KEY_LENGTH - 1))
    _assert_rejected(value, f"ao menos {MIN_SECRET_KEY_LENGTH} caracteres")


# --- BAIXA DIVERSIDADE / REPETIÇÃO ---

def test_repeated_character_key_is_rejected():
    _assert_rejected("a" * 64, "poucos caracteres distintos")


def test_repeated_short_pattern_key_is_rejected():
    _assert_rejected("abc123" * 8, "poucos caracteres distintos")


def test_key_with_too_few_distinct_characters_is_rejected():
    # Alterna apenas entre 5 símbolos -> previsível.
    value = ("abcde" * 10)[:MIN_SECRET_KEY_LENGTH + 8]
    assert len(set(value)) < MIN_DISTINCT_CHARACTERS
    _assert_rejected(value, "poucos caracteres distintos")


# --- PLACEHOLDERS CONHECIDOS ---

@pytest.mark.parametrize(
    "placeholder",
    [
        "generate-a-long-random-secret-before-deploying",  # .env.example
        "your-secret-key-please-change-it-now-123456",
        "CHANGEME-changeme-changeme-changeme-123456",
        "supersecret-supersecret-supersecret-12345",
        "replace-me-with-a-real-key-0123456789abc",
        "placeholder-placeholder-placeholder-99999",
    ],
)
def test_known_placeholders_are_rejected(placeholder):
    _assert_rejected(placeholder, "placeholder")


# --- CHAVES ACEITAS ---

def test_random_csprng_key_is_accepted():
    value = secrets.token_urlsafe(48)
    assert validate_secret_key(value) == value


def test_random_hex_key_is_accepted():
    value = secrets.token_hex(32)
    assert validate_secret_key(value) == value


def test_key_returns_original_value_unchanged():
    """O validador não substitui, não trunca e não normaliza a chave."""
    value = "Kf9_diStinct-Chars_0427" + secrets.token_urlsafe(24)
    assert validate_secret_key(value) is value


def test_minimum_boundary_key_is_accepted():
    # Exatamente o mínimo, com diversidade suficiente.
    value = ("aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1v" * 2)[:MIN_SECRET_KEY_LENGTH]
    assert len(value) == MIN_SECRET_KEY_LENGTH
    assert len(set(value)) >= MIN_DISTINCT_CHARACTERS
    assert validate_secret_key(value) == value


# --- MÓDULO FALHA NO IMPORT SEM CHAVE VÁLIDA ---

def test_module_import_fails_without_valid_secret_key(tmp_path):
    """Prova de startup: sem SECRET_KEY válida o processo não sobe.

    Executa em um diretório temporário (sem `.env`, para que `load_dotenv()`
    não reponha a chave) com o pacote `app` acessível via PYTHONPATH.
    """
    import os
    import pathlib
    import subprocess
    import sys

    repo_root = str(pathlib.Path(__file__).parent.resolve())
    env = dict(os.environ)
    env.pop("SECRET_KEY", None)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    env["PYTHONPATH"] = repo_root

    result = subprocess.run(
        [sys.executable, "-c", "import app.security"],
        cwd=str(tmp_path),  # sem .env aqui
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "o processo não deveria subir sem SECRET_KEY"
    # A mensagem precisa apontar a SECRET_KEY e não conter valor de chave.
    assert "SECRET_KEY" in (result.stdout + result.stderr)


def test_module_import_fails_with_placeholder_key(tmp_path):
    """Placeholder conhecido também impede o startup."""
    import os
    import pathlib
    import subprocess
    import sys

    repo_root = str(pathlib.Path(__file__).parent.resolve())
    env = dict(os.environ)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    env["SECRET_KEY"] = "generate-a-long-random-secret-before-deploying"
    env["PYTHONPATH"] = repo_root

    result = subprocess.run(
        [sys.executable, "-c", "import app.security"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "placeholder" in (result.stdout + result.stderr).lower()
