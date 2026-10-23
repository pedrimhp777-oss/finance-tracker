# Finance Tracker API

API FastAPI para o Finance Tracker. Em produção, use PostgreSQL e configure os segredos por variáveis de ambiente.

## Configuração local

1. Crie um banco PostgreSQL local ou de desenvolvimento.
2. Copie `.env.example` para `.env` e preencha `DATABASE_URL`, `SECRET_KEY` e `FRONTEND_URL`.
3. Instale as dependências com `pip install -r requirements.txt`.
4. Inicie a API com `uvicorn app.main:app --reload`.

O servidor não inicia sem `DATABASE_URL` e `SECRET_KEY`. Gere uma chave forte com `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Não envie o arquivo `.env` ao GitHub.

## Configuração no Render

1. Crie um PostgreSQL gerenciado no mesmo workspace e região do serviço da API.
2. Defina `DATABASE_URL` como a URL interna desse banco, `SECRET_KEY` como uma chave aleatória forte e `FRONTEND_URL` como o domínio de produção do Vercel.
3. Configure o comando de inicialização como `uvicorn app.main:app --host 0.0.0.0 --port $PORT` e o health check como `/health`.
4. Faça o deploy da API e confirme que `/health` responde com `{"status":"ok"}`.

O código aceita os formatos `postgres://` e `postgresql://` do Render e os converte para o driver Psycopg 3.

## Migrar dados do SQLite

O script `migrate_sqlite_to_postgres.py` copia usuários e transações para um banco PostgreSQL vazio, preservando IDs e hashes de senha. Ele não altera nem apaga o SQLite de origem e interrompe a operação se o destino já tiver usuários ou transações.

1. Baixe uma cópia segura do arquivo `finance.db` atual e mantenha um backup.
2. Configure `DATABASE_URL` para o PostgreSQL de destino e instale as dependências.
3. Execute `python migrate_sqlite_to_postgres.py C:\caminho\finance.db`.
4. Confira as contagens de usuários e transações no destino antes de apontar o serviço de produção para o novo banco.

Não coloque `finance.db` no Git nem em arquivos compartilhados sem verificar se contém dados pessoais.

## Variáveis da API

- `DATABASE_URL`: URL de conexão PostgreSQL.
- `SECRET_KEY`: segredo usado para assinar tokens JWT; não há chave padrão de produção.
- `ALGORITHM`: algoritmo JWT (padrão `HS256`).
- `ACCESS_TOKEN_EXPIRE_MINUTES`: duração dos tokens (padrão `30`).
- `FRONTEND_URL`: origem de produção permitida pelo CORS.

