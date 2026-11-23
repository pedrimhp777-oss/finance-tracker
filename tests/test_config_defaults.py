"""Configuração de ambiente: padrão seguro e ordem de leitura do .env.

Os dois cenários só podem ser observados em subprocesso: `app.config` lê o
ambiente no import, e o processo do pytest já tem tudo carregado.
"""
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()


def _run(code: str, env: dict[str, str] | None = None, cwd: pathlib.Path | None = None):
    base = dict(os.environ)
    # O ponto do teste é justamente o que acontece SEM a variável.
    base.pop("APP_ENV", None)
    base.update(env or {})
    base["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd or REPO_ROOT),
        env=base,
        capture_output=True,
        text=True,
    )


def test_sem_app_env_o_padrao_e_producao(tmp_path):
    """Fail closed: sem configuração explícita, o app não abre /docs.

    Antes o padrão era `development`, então um deploy que esquecesse APP_ENV
    expunha a documentação interativa e desligava o modo restrito de produção.
    O teste roda fora do repositório para não herdar o `.env` local.
    """
    result = _run(
        "from app import config; print(config.APP_ENV, config.IS_DEV_ENV)",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr[-800:]
    assert result.stdout.strip() == "production False"


def test_app_env_explicito_vence_o_padrao(tmp_path):
    result = _run(
        "from app import config; print(config.APP_ENV, config.IS_DEV_ENV)",
        env={"APP_ENV": "development"},
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr[-800:]
    assert result.stdout.strip() == "development True"


def test_env_do_diretorio_e_lido_pelo_config(tmp_path):
    """`app.config` carrega o .env antes de ler APP_ENV.

    Antes o `load_dotenv()` ficava no fim do main.py, depois dos imports, então
    APP_ENV/PASSWORD_RESET_URL/TTL do `.env` local eram silenciosamente
    ignorados: o módulo já tinha lido o ambiente do processo.
    """
    (tmp_path / ".env").write_text("APP_ENV=staging\n", encoding="utf-8")

    result = _run("from app import config; print(config.APP_ENV)", cwd=tmp_path)

    assert result.returncode == 0, result.stderr[-800:]
    # Valor que não é nem produção nem desenvolvimento: se o .env continuasse
    # sendo ignorado, a asserção não passaria por coincidência com o padrão.
    assert result.stdout.strip() == "staging"


def test_variavel_de_ambiente_vence_o_env_do_arquivo(tmp_path):
    """Ambiente real (Render/CI) continua tendo prioridade sobre o arquivo."""
    (tmp_path / ".env").write_text("APP_ENV=development\n", encoding="utf-8")

    result = _run(
        "from app import config; print(config.APP_ENV)",
        env={"APP_ENV": "production"},
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr[-800:]
    assert result.stdout.strip() == "production"


def test_trust_proxy_headers_e_opt_in(tmp_path):
    """Aceitar cabeçalho de proxy é decisão explícita, não padrão."""
    padrao = _run("from app import config; print(config.TRUST_PROXY_HEADERS)", cwd=tmp_path)
    assert padrao.stdout.strip() == "False"

    ligado = _run(
        "from app import config; print(config.TRUST_PROXY_HEADERS)",
        env={"TRUST_PROXY_HEADERS": "true"},
        cwd=tmp_path,
    )
    assert ligado.stdout.strip() == "True"
