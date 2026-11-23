"""Diagnóstico de configuração no boot.

Variável de ambiente que não chega ao processo é a falha mais comum em
produção — e a mais difícil de ver, porque o sintoma aparece longe da causa.
O e-mail já tinha esse registro (`services/email.py`); aqui ficam as decisões
de segurança que degradam **em silêncio** quando a configuração está errada.

Nada de valor sensível é registrado: só o estado efetivo de cada opção.
"""

from __future__ import annotations

import logging

from app import config

logger = logging.getLogger(__name__)


def log_security_configuration() -> None:
    """Registra o estado efetivo das opções de segurança do processo.

    `TRUST_PROXY_HEADERS=false` não quebra nada visivelmente: a aplicação segue
    respondendo, mas o rate limit por IP deixa de individualizar clientes —
    todos atrás do proxy compartilham o mesmo balde, e um atacante que envia
    requisições em paralelo não é limitado. É uma degradação silenciosa, então
    ela aparece como WARNING no log de boot.
    """
    if config.TRUST_PROXY_HEADERS:
        logger.info(
            "TRUST_PROXY_HEADERS=true: rate limit por IP usa o IP real do cliente "
            "a partir de CF-Connecting-IP/X-Forwarded-For."
        )
    else:
        logger.warning(
            "TRUST_PROXY_HEADERS=false: cabecalhos de proxy sao ignorados. "
            "Atras de proxy/CDN isso desativa o rate limit por IP (todos os "
            "clientes compartilham um balde). Defina TRUST_PROXY_HEADERS=true "
            "no servico onde ele roda atras de proxy."
        )
