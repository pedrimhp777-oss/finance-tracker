# Finance Tracker — API

Backend do **Finance Tracker**, uma aplicação full-stack de controle financeiro
pessoal. Esta API serve o frontend
[`finance-tracker-front`](https://github.com/pedrimhp777-oss/finance-tracker-front)
(hospedado na [Vercel](https://finance-tracker-front-pearl.vercel.app)).

## Funcionalidades

- 👤 Cadastro e login de usuários (JWT, senhas com bcrypt)
- 💸 CRUD completo de transações (entradas/saídas)
- 🔒 Isolamento total entre usuários (cada usuário acessa só os próprios dados)
- 📊 Resumo financeiro agregado no banco (entradas, saídas, saldo)
- 🗓️ Filtros por mês, ano, categoria e busca por descrição — com suporte a
  fuso horário do usuário
- 📥 Exportação do relatório em Excel (formatado, com sanitização anti-fórmula)
- 🩺 Health check em `/health` e documentação interativa em `/docs` (Scalar)
- 🚦 Rate limiting nas tentativas de login

## Tecnologias

- [FastAPI](https://fastapi.tiangolo.com/) + Pydantic v2
- SQLAlchemy 2 + PostgreSQL ([Neon](https://neon.tech)) / SQLite para testes
- JWT (`python-jose`) + bcrypt (`passlib`)
- OpenPyXL para exportação Excel
- Alembic para migrações versionadas
- Uvicorn

## Estrutura

```
app/
├── main.py                  # Aplicação FastAPI, CORS, health e docs
├── database.py              # Engine, sessões e Base declarativa
├── models.py                # Modelos ORM (users, transactions)
├── schemas.py               # Schemas Pydantic (validação de entrada/saída)
├── security.py              # Hash de senha, JWT e rate limiting
├── routes/
│   ├── auth.py              # /auth/register, /auth/login, /auth/me
│   └── transactions.py      # CRUD, /summary, /export/excel
└── services/
    ├── transactions.py      # Consultas com filtros/fuso e agregação do resumo
    └── excel_export.py      # Geração da planilha
migrations/                  # Migrações Alembic
test_*.py                    # Suíte de testes (SQLite em memória isolado)
```

## Requisitos

- Python 3.12+

## Instalação e execução local

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash (Linux: .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # preencha as variáveis (ver abaixo)
uvicorn app.main:app --reload
```

A API sobe em `http://localhost:8000` (documentação em `/docs`).

### Variáveis de ambiente (.env)

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | String de conexão PostgreSQL (ou SQLite). Ex: `postgresql://user:senha@host/db` |
| `SECRET_KEY` | Segredo do JWT (mínimo 32 caracteres) |
| `ALGORITHM` | Algoritmo JWT (padrão `HS256`) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Expiração do token (padrão 30) |
| `FRONTEND_URL` | Origem do frontend liberada no CORS |
| `APP_ENV` | `development` libera também `localhost:5173` no CORS |

## Testes

```bash
python -m pytest -q
```

Os testes **sempre** usam SQLite em memória, isolados do banco configurado
no ambiente (garantido pelo `conftest.py`, que sobrescreve `DATABASE_URL`).

## Backup lógico

O **Instant Restore** do Neon cobre ~6h e protege contra erro humano — mas não
contra perder a conta, o projeto ou o provedor. Para isso existe uma cópia
independente:

```bash
# gera backups/backup-AAAAMMDD-HHMMSS.json.gz (somente leitura no banco)
python scripts/backup.py

# diretório externo (recomendado: fora do provedor do banco)
python scripts/backup.py --out D:/backups

# confere a integridade de um backup já gerado
python scripts/backup.py --verify backups/backup-2026-11-15.json.gz
```

O arquivo é JSON comprimido com `meta` (timestamp, versão Alembic, contagem por
tabela) e `tables` (as linhas). Detalhes que evitam perda silenciosa:

- `amount` é gravado como **string**, não float — centavos não são arredondados;
- datas em **ISO 8601**; `bytes` em base64;
- o **hash bcrypt** da senha é preservado (sem ele o backup não restaura login);
- `--verify` compara a contagem real com a declarada na meta e falha se divergir.

> ⚠️ O arquivo contém dados pessoais (e-mails e hashes de senha). Guarde em
> local protegido, **nunca** no Git. O diretório `backups/` já está no
> `.gitignore`.

## Monitoramento de erros (opcional)

Sem `SENTRY_DSN`, a aplicação funciona exatamente como antes: nenhuma chamada
de rede, nenhum overhead. Definindo a variável, erros 5xx passam a ser
registrados com traceback — hoje eles morrem no log efêmero da Render.

```bash
SENTRY_DSN=https://<chave>@<org>.ingest.sentry.io/<projeto>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0   # 0 = sem tracing de performance
```

### O que **nunca** é enviado

Esta é uma API financeira: o evento de erro não pode ser mais sensível que a
aplicação. A proteção é em camadas (`app/observability.py`):

| Camada | Efeito |
|---|---|
| `max_request_body_size="never"` | o SDK não coleta corpo de requisição |
| `send_default_pii=False` | sem IP, cookies ou headers de usuário |
| `before_send` | remove `data`, `cookies` e `query_string`; apaga `user` |
| `before_breadcrumb` | limpa payloads em breadcrumbs HTTP/log |

Headers mascarados: `Authorization`, `Cookie`, `X-CSRF-Token`, `X-API-Key`,
`Proxy-Authorization`, `X-Auth-Token`.

Na prática, um erro em `/auth/login` **não** leva a senha, e um erro em
`/transactions` **não** leva descrição, categoria ou valor.

## Migrações (Alembic)
```bash
# Ambiente novo (cria o schema completo):
alembic upgrade head

# Banco que já possui as tabelas (produção atual, criada via create_all):
# 1) marca o estado atual como 0001 (apenas registra, não executa DDL):
alembic stamp 0001_initial
# 2) aplica as migrações incrementais (ex.: índices da 0002):
alembic upgrade head
```

A migração inicial está em `migrations/versions/0001_initial.py`; os índices
de desempenho estão em `migrations/versions/0002_indexes.py`.

## Endpoints principais

| Método | Rota | Descrição |
|---|---|---|
| POST | `/auth/register` | Cadastro (201; e-mail duplicado → 409) |
| POST | `/auth/login` | Login (OAuth2 form; retorna JWT) |
| GET | `/auth/me` | Dados do usuário autenticado |
| POST | `/auth/logout` | Encerra a sessão revogando **todos** os tokens do usuário |
| POST | `/transactions/` | Criar transação |
| GET | `/transactions/` | Listar com filtros (`month`, `year`, `category`, `search`, `timezone_name`, `skip`, `limit`) |
| GET | `/transactions/summary` | Resumo financeiro agregado |
| GET | `/transactions/export/excel` | Exportação Excel com os mesmos filtros |
| PUT/DELETE | `/transactions/{id}` | Atualizar / excluir |

## Autenticação — estado atual e limites

### Modo ativo: `Authorization: Bearer`

O frontend guarda o JWT no `localStorage` e o envia no header `Authorization`.
Requisições com esse header **não passam por verificação de CSRF**, porque um
site terceiro não consegue definir esse header em formulários ou navegações.

**Risco conhecido e aceito:** em caso de XSS, um script poderia ler o token.
As mitigações aplicadas são a ausência de vetores de injeção no frontend
(nenhum `dangerouslySetInnerHTML`/`innerHTML`/`eval`), React escapando texto
por padrão e a CSP configurada no deploy do frontend.

### Modo alternativo: cookie `HttpOnly` (opt-in, **inativo** hoje)

O backend também aceita sessão por cookie: no login ele emite
`access_token` (`HttpOnly`, não legível por JS) e `csrf_token` (legível, para
*o double-submit*). A troca de estado exige o header `X-CSRF-Token` igual ao
cookie. O header `Authorization` tem **precedência** sobre o cookie.

Esse modo está implementado e testado, mas **não é usado pelo frontend**:

1. o Axios não envia credenciais em requisições cross-origin
   (`withCredentials` desligado) — por isso o navegador nem inclui os cookies;
2. API e frontend vivem em domínios registráveis diferentes
   (`*.onrender.com` e `*.vercel.app`), o que torna o cookie de terceira
   parte — e Safari/Firefox já bloqueiam esse tipo por padrão.

**Pré-requisitos para migrar** (no dia em que houver um domínio próprio):

- servir API e frontend sob o mesmo domínio registrável (ex.: `app.` e `api.`);
- definir `Domain` no cookie de CSRF **ou** expor um `GET /auth/csrf`,
  senão o JavaScript do frontend não consegue ler o token para ecoá-lo;
- ligar `withCredentials` no cliente HTTP.

### Logout revoga todas as sessões

O `token_version` é **por usuário**, não por dispositivo. Um `POST /auth/logout`
(inclusive o disparado pelo botão "Sair") invalida **todos** os tokens daquela
conta — os outros dispositivos precisam autenticar novamente. É intencional
(um logout serve como "sair de tudo" em caso de suspeita), mas vale saber ao
usar várias abas ou aparelhos.

O frontend dispara o logout em modo *best-effort*: se a requisição não chegar
ao servidor, a revogação não acontece e o token continua válido até expirar
(TTL padrão: 30 minutos).

### Headers de resposta

| Header | Onde | Objetivo |
|---|---|---|
| `Cache-Control: no-store` | `/transactions*`, `/auth/login`, `/auth/register`, `/auth/me`, `/auth/logout` | impedir cache de dados financeiros/pessoais e de tokens |
| `X-Content-Type-Options: nosniff` | toda resposta | impedir que o navegador "adivinhe" o tipo do corpo |
| `Access-Control-Allow-Origin` | respostas com `Origin` permitida, inclusive **erros 500** | o `ServerErrorMiddleware` responde fora da pilha; sem isso o navegador bloquearia o erro |

## Deploy

- Backend: [Render](https://render.com) (`uvicorn app.main:app`)
- Banco: PostgreSQL no [Neon](https://neon.tech)
- Frontend: [Vercel](https://vercel.com) — repositório
  [`finance-tracker-front`](https://github.com/pedrimhp777-oss/finance-tracker-front)

CI: GitHub Actions executa a suíte de testes e valida a migração Alembic
em banco isolado a cada push/PR.
