# Finance Tracker API

API FastAPI para o Finance Tracker. Em produ��o, use PostgreSQL e configure os segredos por vari�veis de ambiente.

## Configura��o local

1. Crie um banco PostgreSQL local ou de desenvolvimento.
2. Copie `.env.example` para `.env` e preencha `DATABASE_URL`, `SECRET_KEY` e `FRONTEND_URL`.
3. Instale as depend�ncias com `pip install -r requirements.txt`.
4. Inicie a API com `uvicorn app.main:app --reload`.

O servidor n�o inicia sem `DATABASE_URL` e `SECRET_KEY`. Gere uma chave forte com `python -c "import secrets; print(secrets.token_urlsafe(48))"`. N�o envie o arquivo `.env` ao GitHub.

## Configura��o no Render

1. Crie um PostgreSQL gerenciado no mesmo workspace e regi�o do servi�o da API.
2. Defina `DATABASE_URL` como a URL interna desse banco, `SECRET_KEY` como uma chave aleat�ria forte e `FRONTEND_URL` como o dom�nio de produ��o do Vercel.
3. Configure o comando de inicializa��o como `uvicorn app.main:app --host 0.0.0.0 --port $PORT` e o health check como `/health`.
4. Fa�a o deploy da API e confirme que `/health` responde com `{"status":"ok"}`.

O c�digo aceita os formatos `postgres://` e `postgresql://` do Render e os converte para o driver Psycopg 3.

## Migrar dados do SQLite

O script `migrate_sqlite_to_postgres.py` copia usu�rios e transa��es para um banco PostgreSQL vazio, preservando IDs e hashes de senha. Ele n�o altera nem apaga o SQLite de origem e interrompe a opera��o se o destino j� tiver usu�rios ou transa��es.

1. Baixe uma c�pia segura do arquivo `finance.db` atual e mantenha um backup.
2. Configure `DATABASE_URL` para o PostgreSQL de destino e instale as depend�ncias.
3. Execute `python migrate_sqlite_to_postgres.py C:\caminho\finance.db`.
4. Confira as contagens de usu�rios e transa��es no destino antes de apontar o servi�o de produ��o para o novo banco.

N�o coloque `finance.db` no Git nem em arquivos compartilhados sem verificar se cont�m dados pessoais.

## Vari�veis da API

- `DATABASE_URL`: URL de conex�o PostgreSQL.
- `SECRET_KEY`: segredo usado para assinar tokens JWT; n�o h� chave padr�o de produ��o.
- `ALGORITHM`: algoritmo JWT (padr�o `HS256`).
- `ACCESS_TOKEN_EXPIRE_MINUTES`: dura��o dos tokens (padr�o `30`).
- `FRONTEND_URL`: origem de produ��o permitida pelo CORS.
