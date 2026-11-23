# Finance Tracker — API

> API REST para controle financeiro pessoal: autenticação com revogação de sessão,
> dados isolados por usuário, filtros no servidor e exportação Excel.

[![CI](https://github.com/pedrimhp777-oss/finance-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/pedrimhp777-oss/finance-tracker/actions/workflows/ci.yml)

**Frontend:** [finance-tracker-front](https://github.com/pedrimhp777-oss/finance-tracker-front) · **Aplicação:** [finance-tracker-front-pearl.vercel.app](https://finance-tracker-front-pearl.vercel.app) · **API:** [finance-tracker-api-7d8o.onrender.com](https://finance-tracker-api-7d8o.onrender.com/health)

---

## Sobre

Backend de uma aplicação full-stack de finanças pessoais. O objetivo do projeto
não foi "fazer um CRUD", e sim construir a base com as decisões que um sistema
real exige: migrações versionadas, isolamento entre usuários, sessões
revogáveis, respostas sem cache, testes de segurança e observabilidade.

Cada decisão relevante está documentada no código e nas seções abaixo —
inclusive as **limitações conhecidas**.

## Stack

| Camada | Tecnologia |
|---|---|
| Framework | FastAPI 0.141 |
| ORM / banco | SQLAlchemy 2.0 + PostgreSQL 18 (Neon) |
| Migrações | Alembic (4 revisões) |
| Validação | Pydantic v2 |
| Auth | PyJWT (HS256) + bcrypt 5.0 |
| Excel | openpyxl (streaming) |
| Observabilidade | Sentry SDK (opcional, sem PII) |
| Deploy | Render + Cloudflare |

## Arquitetura

```
app/
├── main.py              # composição: middlewares, rotas, handlers globais
├── config.py            # configuração derivada de ambiente (fonte única)
├── security.py          # JWT, hashing, rate limit, get_current_user
├── middleware.py        # no-store por escopo + nosniff (ASGI puro)
├── observability.py     # Sentry com scrubbing de PII
├── models.py            # User, Transaction, PasswordResetToken
├── schemas.py           # contratos de entrada/saída
├── routes/              # auth.py, transactions.py
└── services/            # regras de negócio: transactions, excel_export, email
migrations/versions/     # 0001 → 0004
scripts/backup.py        # dump lógico independente do provedor
```

**Fluxo de uma requisição autenticada**

```
Cliente → CORS → nosniff → no-store (rotas sensíveis) → rota
                                                       ↓
                                        Depends(get_current_user)
                                          header Authorization: Bearer
                                                       ↓
                              valida JWT → confere token_version no banco
                                                       ↓
                                        consulta sempre filtrada por user_id
```

Duas escolhas de implementação que valem nota:

**Middlewares em ASGI puro, não `BaseHTTPMiddleware`.** A exportação Excel é um
`StreamingResponse`; middlewares baseados em `BaseHTTPMiddleware` consomem e
reenvelopam o corpo, o que pode corromper downloads grandes. Aqui os headers são
aplicados interceptando apenas o evento `http.response.start`.

**O schema é propriedade exclusiva do Alembic.** `Base.metadata.create_all()`
foi removido: banco criado por código diverge do banco de produção silenciosamente.

## Funcionalidades

- Cadastro e login (JWT) com senhas em **bcrypt**
- **Revogação de sessão** server-side via `token_version`
- Recuperação de senha por e-mail (token de uso único, 30 min)
- CRUD de transações com isolamento por usuário
- Resumo financeiro (entradas, saídas, saldo) agregado no banco
- Filtros por mês, ano, **dia**, categoria e busca — aplicados no servidor
- Paginação e filtro de período em SQL, no servidor
- Exportação **Excel** respeitando exatamente os filtros e o fuso do cliente

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/auth/register` | Cadastro (`201`, e-mail duplicado → `409`) |
| `POST` | `/auth/login` | Login OAuth2 form → JWT no corpo |
| `POST` | `/auth/logout` | Revoga **todas** as sessões do usuário |
| `GET` | `/auth/me` | Dados do usuário autenticado |
| `POST` | `/auth/forgot-password` | Solicita link de redefinição (sempre `202`) |
| `POST` | `/auth/reset-password` | Troca a senha com o token do e-mail |
| `POST` | `/transactions/` | Cria transação |
| `GET` | `/transactions/` | Lista com filtros e paginação |
| `GET` | `/transactions/summary` | Resumo agregado |
| `GET` | `/transactions/categories` | Totais por categoria |
| `GET` | `/transactions/{id}` | Detalhe |
| `PUT` | `/transactions/{id}` | Atualiza |
| `DELETE` | `/transactions/{id}` | Exclui |
| `GET` | `/transactions/export/excel` | Exportação `.xlsx` com os filtros aplicados |
| `GET` | `/health` | Verificação com `SELECT 1` no banco |

> `GET /docs` e `/openapi.json` ficam **desativados** quando `APP_ENV=production`.

## Autenticação

### `Authorization: Bearer`

O frontend guarda o JWT no `localStorage` e envia no header. Requisições assim
**não precisam de token CSRF**: um site terceiro não consegue definir um header
customizado — formulários e navegações só carregam cookies e corpo, e o CORS
barra a leitura da resposta.

**Risco aceito:** em caso de XSS, um script poderia ler o token. As mitigações
aplicadas são ausência de vetores de injeção no frontend (nenhum
`dangerouslySetInnerHTML`/`innerHTML`/`eval`), escape padrão do React e **CSP
em modo enforcing** no deploy.

### Por que não existe sessão por cookie

Havia um segundo modo, opt-in: cookie `HttpOnly` + CSRF double-submit. Ele foi
**removido** porque exigiria API e frontend no mesmo domínio registrável
(`app.exemplo.com` + `api.exemplo.com`). Como hoje são `*.vercel.app` e
`*.onrender.com`, o cookie seria de terceira parte e o navegador o descartaria.

Manter o caminho sem uso custava mais do que rendia: código de autenticação que
nunca roda em produção envelhece sem ser exercitado e sugere uma proteção que
não está ativa. A implementação está no histórico do Git, caso um dia haja
domínio próprio.

### Revogação de sessão

O JWT carrega a claim `tv` (token version). `get_current_user` compara com
`users.token_version` no banco a cada requisição.

- `POST /auth/logout` incrementa o contador → **todos** os tokens da conta morrem
- Troca de senha faz o mesmo (se o motivo foi invasão, o token do invasor cai junto)
- Tokens antigos sem `tv` valem como `0`; `tv` não-inteiro é rejeitado

Estado no banco, não em memória: sobrevive a reinícios e funciona com múltiplas
instâncias, sem custo extra (o usuário já é carregado).

> ⚠️ O logout é **por usuário, não por dispositivo** — os outros aparelhos
> precisam autenticar de novo. É intencional ("sair de tudo"), mas vale saber.

## Recuperação de senha

| Rota | Comportamento |
|---|---|
| `POST /auth/forgot-password` | **sempre `202`**, mesma mensagem, exista ou não a conta |
| `POST /auth/reset-password` | `204` ao trocar · `400` genérico (inválido/expirado/usado) |

- O token em claro (256 bits) **só existe no e-mail**; o banco guarda apenas `SHA-256`
- Uso único + expiração de **30 min**; pedir um link novo invalida os anteriores
- A troca **incrementa `token_version`** e derruba todas as sessões
- Rate limit por **IP e por e-mail** (10 / 5 min)
- O link nunca aparece no log em produção

### Provedor de e-mail

Dois provedores, ambos por HTTP puro (sem SDK):

| Provedor | Verificação exigida | Observação |
|---|---|---|
| **Brevo** | **um endereço de e-mail** | recomendado: não exige domínio próprio; 300 envios/dia no free |
| Resend | domínio verificado (SPF/DKIM) | alternativa, melhor entregabilidade com domínio |

`BREVO_API_KEY` tem prioridade se ambas as chaves existirem. `EMAIL_FROM` aceita
`Nome <email>` **ou** apenas `email`.

Sem chave configurada, a rota continua respondendo `202` (não vaza existência de
conta) mas **nenhum e-mail sai** — em desenvolvimento o link vai para o console,
e na inicialização o log informa se o envio está configurado.

```bash
EMAIL_FROM="Finance Tracker <seu-email@gmail.com>"
BREVO_API_KEY=xkeysib-...
PASSWORD_RESET_URL=https://finance-tracker-front-pearl.vercel.app
PASSWORD_RESET_TTL_MINUTES=30
```

## Segurança

| Área | Implementação |
|---|---|
| Senhas | bcrypt 5.0 direto (custo 12); senhas > 72 bytes recusadas, não truncadas |
| Chave de assinatura | validada no boot: rejeita vazia, `placeholder`, `< 32` chars e `< 8` chars distintos |
| Rate limiting | 3 buckets por janela deslizante de 5 min: `ip:email` (10), e-mail (20) e IP (60); LRU com teto de 10k chaves |
| Cache | `Cache-Control: no-store` em `/transactions*` e nas rotas de auth |
| MIME | `X-Content-Type-Options: nosniff` em toda resposta |
| CORS | allowlist explícita, **inclusive nas respostas de erro 500** |
| Erros | `500` genérico, sem stack trace nem detalhes internos |
| Documentação | `/docs` e `/openapi.json` fechados em produção |
| Isolamento | toda consulta filtrada por `user_id`; acesso indevido devolve `404` (não `403`) |
| Enumeração | login roda o bcrypt mesmo para e-mail inexistente (hash falso de mesmo custo) e `forgot-password` envia fora do caminho da resposta — o tempo não denuncia contas. `register` devolve `409` de propósito (UX > sigilo de existência) |
| Segredos | `.env` fora do Git; nenhuma credencial no histórico |

**Detalhe que costuma passar:** o `ServerErrorMiddleware` do Starlette roda
**fora** do `CORSMiddleware`. Sem tratamento explícito, um `500` sairia sem
`Access-Control-Allow-Origin` e o navegador bloquearia a resposta — o usuário
veria "erro de conexão" em vez de uma mensagem. O handler global aplica os
headers de CORS (espelhando **apenas** origens da allowlist) e `no-store`.

## Modelo de dados

```
users                     transactions                password_reset_tokens
─────                     ────────────                ─────────────────────
id                        id                          id
name                      description                 user_id  → users (CASCADE)
email (unique)            amount NUMERIC(12,2) > 0    token_hash (SHA-256, unique)
hashed_password           type  (income|expense)      expires_at
token_version             category                    used_at
created_at                date                        created_at
                          user_id → users
```

Índices compostos `(user_id, date)` e `(user_id, type)` atendem as consultas mais
frequentes de listagem e resumo.

## Migrações

```bash
alembic upgrade head            # aplica até a última revisão
alembic current                 # revisão atual
alembic revision --autogenerate -m "descrição"
```

| Revisão | Conteúdo |
|---|---|
| `0001_initial` | `users`, `transactions` |
| `0002_indexes` | índices `(user_id, date)` e `(user_id, type)` |
| `0003_token_version` | coluna aditiva para revogação de sessão |
| `0004_password_reset` | tabela de tokens de redefinição |

Para um banco que já tinha as tabelas (criadas por versões antigas):
`alembic stamp 0001_initial` → `alembic upgrade head`.

> Em DDL, use a conexão **direta** do Neon (sem `-pooler`): poolers podem
> interferir em operações de schema.

## Backup

O *Instant Restore* do Neon cobre ~6h e protege contra erro humano — não contra
perder a conta ou o provedor.

```bash
python scripts/backup.py                    # backups/backup-AAAAMMDD-HHMMSS.json.gz
python scripts/backup.py --out D:/backups   # fora do provedor
python scripts/backup.py --verify backups/backup-2026-11-15.json.gz
```

Somente leitura no banco. `amount` é gravado como **string** (float arredondaria
centavos), datas em ISO 8601, `bytes` em base64 e o **hash bcrypt é preservado**
(sem ele o backup restaura dados mas ninguém consegue logar). `--verify` compara
a contagem real com a declarada na meta e falha se divergir.

> ⚠️ O arquivo contém dados pessoais. Guarde fora do Git — `backups/` já está no `.gitignore`.

## Observabilidade

Opcional e desligado por padrão: sem `SENTRY_DSN` não há chamada de rede nem
overhead.

```bash
SENTRY_DSN=https://<chave>@<org>.ingest.sentry.io/<projeto>
SENTRY_TRACES_SAMPLE_RATE=0   # performance desligada
```

Sendo uma API financeira, um evento de erro não pode ser mais sensível que a
aplicação. Proteção em camadas:

| Camada | Efeito |
|---|---|
| `max_request_body_size="never"` | o SDK não coleta corpo de requisição |
| `send_default_pii=False` | sem IP, cookies ou headers de usuário |
| `before_send` | remove `data`, `cookies`, `query_string`; apaga `user` |
| `before_breadcrumb` | limpa payloads de HTTP/log |

Headers mascarados: `Authorization`, `Cookie`, `X-CSRF-Token`, `X-API-Key`.
Um erro em `/auth/login` não leva a senha; em `/transactions`, não leva valor,
descrição nem categoria.

## Testes

```bash
# Uma vez: cria o banco de teste (vazio)
createdb finance_tracker_test

python -m pytest -q          # 261 testes
```

A suíte roda contra um **PostgreSQL real**, o mesmo motor da produção.
`conftest.py` sobrescreve `DATABASE_URL` com `TEST_DATABASE_URL` (padrão
`postgresql+psycopg://postgres:postgres@localhost:5432/finance_tracker_test`),
aplica as migrações com `alembic upgrade head` e limpa as tabelas entre os
testes. Nenhum teste toca o banco de produção.

Até o B1 a suíte rodava em SQLite em memória. Além de testar outro motor, isso
obrigava o serviço a manter dois caminhos de consulta — e deixava justamente o
caminho de produção sem cobertura. Foi assim que um `GROUP BY` inválido passou
pelos testes e só quebrou no deploy.

| Arquivo | Cobre |
|---|---|
| `test_auth`, `test_auth_security` | cadastro, login, senha errada, token inválido |
| `test_transactions`, `test_transactions_filters` | CRUD, filtros, paginação, **isolamento entre usuários** |
| `test_security_headers` | escopo de `no-store`, preflight, `OPTIONS` |
| `test_cors_on_500` | CORS em erro não tratado |
| `test_session_revocation` | `tv` inválido, revogação, tokens legados |
| `test_password_reset` | enumeração, uso único, expiração, rate limit, provedores |
| `test_password_hashing` | bcrypt e **compatibilidade com hashes do passlib** |
| `test_secret_key_validation` | chaves fracas e placeholders |
| `test_observability` | scrubbing de PII |
| `test_backup_script` | dump, integridade e detecção de corrupção |
| `test_client_ip` | resolução de IP atrás de proxy/Cloudflare |

## Rodando local

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # preencha DATABASE_URL e SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000/docs
```

### Variáveis de ambiente

| Variável | Obrigatória | Função |
|---|---|---|
| `DATABASE_URL` | ✅ | conexão PostgreSQL |
| `SECRET_KEY` | ✅ | assinatura JWT — validada no boot |
| `FRONTEND_URL` | ✅ | origem permitida no CORS |
| `APP_ENV` | — | padrão **`production`** (fail closed: fecha `/docs` e `/openapi.json`); `development` abre |
| `TRUST_PROXY_HEADERS` | recomendada na Render | confia em `CF-Connecting-IP`/`X-Forwarded-For` para obter o IP real; padrão `false` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | padrão `30` |
| `EMAIL_FROM`, `BREVO_API_KEY` | para e-mail | recuperação de senha |
| `PASSWORD_RESET_URL` | para e-mail | base do link enviado |
| `SENTRY_DSN` | — | monitoramento (opcional) |

## Deploy

**Render** (API) + **Neon** (PostgreSQL) + **Vercel** (frontend).

```bash
# Render
Build:  pip install -r requirements.txt
Start:  alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Checklist de produção:

- [ ] `APP_ENV=production` (fecha `/docs` e `/openapi.json`)
- [ ] `TRUST_PROXY_HEADERS=true` na Render — sem isso o IP visto é o do proxy e o bucket de rate limit por IP fica desligado (buckets por e-mail e por `ip:email` seguem ativos)
- [ ] `SECRET_KEY` de 64 hex gerada com `secrets.token_hex(32)`
- [ ] `FRONTEND_URL` com o domínio exato do frontend
- [ ] `alembic upgrade head` aplicado
- [ ] backup restaurável testado
- [ ] variáveis de e-mail/E2E validadas nos logs

## Decisões e trade-offs

| Decisão | Motivo | Custo aceito |
|---|---|---|
| `token_version` no banco (não denylist em memória) | sobrevive a restart e escala horizontal | 1 comparação por requisição (usuário já carregado) |
| Um único caminho PostgreSQL; suíte roda no banco real | o caminho de produção passa a ser testado, e o bug de `GROUP BY` não se repete | testes exigem um Postgres local (ou o serviço do CI) |
| Rate limiter em memória | zero dependência para uma instância | **por processo** — não escala horizontalmente sem Redis |
| Sessão só por `Bearer`, sem cookie | frontend e API em domínios registráveis diferentes: o cookie seria de terceira parte e o navegador o descarta | token no `localStorage`; XSS mitigado por CSP enforcing e ausência de injeção |
| bcrypt direto no lugar do passlib | passlib 1.7.4 (2020) quebra com bcrypt ≥ 4.1 e travava atualizações | API de hashing mantida pequena e testada |
| CSP estática (sem nonce) | hospedagem estática não permite nonce | `style-src 'unsafe-inline'` é necessário para o Recharts |

## Limitações conhecidas

- JWT em `localStorage` (mitigado por CSP e ausência de vetores de XSS)
- Logout é por usuário, não por dispositivo
- `register` revela indiretamente contas existentes (`409`)
- Rate limiter não é compartilhado entre processos
- Recuperação de senha exige provedor verificado para alcançar qualquer usuário
