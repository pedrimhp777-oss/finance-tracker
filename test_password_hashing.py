"""Testes do hashing de senha com bcrypt direto (sem passlib).

Contexto da mudança: o passlib 1.7.4 (última release, de 2020) está sem
manutenção e **quebra** com bcrypt >= 4.1 — ele lê `bcrypt.__about__`, que foi
removido, e não lida com o limite de 72 bytes virar exceção. Manter o passlib
obrigava a fixar `bcrypt==4.0.1`, impedindo qualquer atualização de segurança
do bcrypt.

O ponto crítico desta mudança é **não perder sessão de usuário**: todos os
hashes gravados pelo passlib estão no formato `$2b$`, e precisam continuar
validando. O hash "legado" abaixo foi realmente produzido pelo passlib 1.7.4
(com bcrypt 4.0.1) antes da troca — não é um hash inventado.
"""
import pytest

from app.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    hash_password,
    verify_password,
)

# Produzido por passlib.context.CryptContext(schemes=["bcrypt"]) em 2026-11-15.
LEGACY_PASSLIB_HASH = "$2b$12$UBNG17X0LZOKHUmgSMTjF.xMj108ERXnRCSFbsiinSrPMxQlcWYGi"
LEGACY_PASSWORD = "senha-legada-123"


# ------------------------------------------------------------ compatibilidade

def test_hash_legado_do_passlib_continua_valido():
    """Nenhum usuário existente perde acesso por causa desta mudança."""
    assert verify_password(LEGACY_PASSWORD, LEGACY_PASSLIB_HASH) is True


def test_hash_legado_rejeita_senha_errada():
    assert verify_password("senha-errada", LEGACY_PASSLIB_HASH) is False


# ------------------------------------------------------------ roundtrip

def test_hash_e_verificacao_funcionam():
    hashed = hash_password("minha-senha-123")
    assert verify_password("minha-senha-123", hashed) is True
    assert verify_password("minha-senha-124", hashed) is False


def test_formato_do_hash_e_bcrypt_2b():
    hashed = hash_password("qualquer-senha")
    assert hashed.startswith("$2b$12$")
    assert len(hashed) == 60  # formato bcrypt fixo


def test_hashes_da_mesma_senha_sao_diferentes():
    """Salt aleatório: dois hashes da mesma senha não podem ser iguais."""
    a = hash_password("repetida-123")
    b = hash_password("repetida-123")
    assert a != b
    assert verify_password("repetida-123", a)
    assert verify_password("repetida-123", b)


def test_senha_com_acento_e_unicode():
    senha = "senhã-com-acentuação-ç"
    assert verify_password(senha, hash_password(senha)) is True


# ------------------------------------------------------------ limites

def test_senha_de_72_bytes_e_aceita():
    senha = "a" * BCRYPT_MAX_PASSWORD_BYTES
    assert verify_password(senha, hash_password(senha)) is True


def test_senha_acima_de_72_bytes_e_recusada():
    """Truncar silenciosamente faria senhas diferentes colidirem."""
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password("a" * (BCRYPT_MAX_PASSWORD_BYTES + 1))


def test_senha_longa_na_verificacao_falha_sem_estourar():
    """Na 5.x o bcrypt levanta ValueError; aqui isso vira False, não 500."""
    assert verify_password("a" * 100, LEGACY_PASSLIB_HASH) is False


# ------------------------------------------------------------ robustez

@pytest.mark.parametrize(
    "hash_invalido",
    [
        "",
        "nao-e-um-hash",
        "$2b$12$curto",
        "$2x$12$" + "a" * 53,
        "$2b$12$" + "!" * 53,
    ],
)
def test_hash_invalido_nao_levanta_excecao(hash_invalido):
    assert verify_password("qualquer", hash_invalido) is False


@pytest.mark.parametrize("senha", ["", None])
def test_senha_vazia_falha(senha):
    assert verify_password(senha, LEGACY_PASSLIB_HASH) is False
