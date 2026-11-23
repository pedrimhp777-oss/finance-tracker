"""Guarda do layout do repositório.

Motivo: o PR #26 criou `test_account_data.py` na raiz e o PR #28, que moveu a
suíte para `tests/`, foi escrito antes — o merge dos dois deixou o arquivo na
raiz, importando `test_transactions` (que agora vive em `tests/`). O resultado
foi uma suíte quebrada no `main`, com o CI do PR verde: o CI da branch roda no
commit do PR e não vê o que o `main` ganhou depois.

Este teste custa milissegundos e fecha a porta: arquivo de teste fora de
`tests/` quebra a coleta antes de chegar em produção.
"""

import pathlib

RAIZ = pathlib.Path(__file__).parent.parent


def test_nenhum_arquivo_de_teste_fora_de_tests():
    soltos = sorted(p.name for p in RAIZ.glob("test_*.py"))
    assert soltos == [], (
        f"arquivo(s) de teste fora de tests/: {soltos}. "
        "Mova para tests/ — os testes importam entre si pelo nome do módulo, "
        "e a raiz não está no sys.path da coleta."
    )


def test_conftest_fica_na_raiz():
    """O conftest precisa rodar antes de qualquer import da aplicação.

    É ele que define `DATABASE_URL` e `BCRYPT_ROUNDS`; `app.config` lê o
    ambiente no import. Dentro de `tests/`, chegaria tarde.
    """
    assert (RAIZ / "conftest.py").is_file()
    assert not (RAIZ / "tests" / "conftest.py").exists()


def test_suite_vive_em_tests():
    assert (RAIZ / "tests" / "test_transactions.py").is_file()
    assert (RAIZ / "scripts" / "backup.py").is_file()
