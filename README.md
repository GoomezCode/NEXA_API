# Nexa API

Backend do chat de IA via web, em **Python/FastAPI**.

- **Versão reportada pelo pacote:** `1.1.0` (`app.__version__`); a implementação
  atual ainda não foi publicada nem etiquetada como release `2.0.0`.
- **Funcionalidades no código:** gateway multi-provedor, compatibilidade
  OpenAI/Anthropic/Ollama, NVIDIA NIM com chave e URL base configuráveis,
  SQLite para contas, cadastro público configurável, personalização do chat e
  chaves BYOK criptografadas.
- **Estado de entrega:** critérios funcionais automatizados em boa parte; release
  permanece pendente de hardening, validação operacional e atualização final dos
  documentos de aceitação.

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/srs-api-chat-ia-v1.md`](docs/srs-api-chat-ia-v1.md) | SRS v2.0 (especificação) |
| [`docs/plano-implementacao-api-chat-ia-v1.md`](docs/plano-implementacao-api-chat-ia-v1.md) | Plano de implementação v2.0 |
| [`docs/provedores-ia.md`](docs/provedores-ia.md) | Provedores, Model IDs, fallback e BYOK |
| [`docs/compatibilidade-apis-externas.md`](docs/compatibilidade-apis-externas.md) | Contratos OpenAI, Anthropic (Claude Code) e Ollama |
| [`docs/database-sqlite.md`](docs/database-sqlite.md) | Schema, ADMIN inviolável, PRAGMAs e performance |
| [`docs/matriz-ca-testes.md`](docs/matriz-ca-testes.md) | Rastreabilidade CA-01..CA-47 |
| [`docs/checklist-release.md`](docs/checklist-release.md) | Histórico v1.1.0 e checklist v2.0 ainda aberto |

## Requisitos

- Python 3.11+ (o CI usa 3.12)
- `make` (opcional)
- Docker + Docker Compose (opcional, para a imagem de produção)
- Ollama local ou credenciais de um provedor de nuvem, como NVIDIA NIM

## Configuração

```bash
cp .env.example .env   # ajuste ADMIN_PASSWORD_HASH e JWT_SECRET
make setup             # cria .venv e instala dependências
```

Gere o hash da senha do administrador:

```bash
.venv/bin/python scripts/hash_password.py
```

Em produção (`ENVIRONMENT=production`), `JWT_SECRET` e `KEY_ENCRYPTION_SECRET`
precisam ter ao menos 32 caracteres e não podem ser valores de exemplo. O
segredo de criptografia protege as chaves BYOK persistidas no SQLite. `UVICORN_WORKERS > 1`
exige `REDIS_URL`.

### Variáveis principais

Todas vêm do ambiente ou de um `.env` na raiz (12-factor). O `.env.example`
lista as variáveis lidas pela implementação atual.

| Variável | Padrão | Descrição |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` ou `production` |
| `LOG_LEVEL` | `INFO` | Nível dos logs JSON |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | — | Credenciais do administrador (obrigatórias) |
| `JWT_SECRET` | — | Segredo de assinatura dos tokens |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | `60` | Validade do token de admin |
| `GUEST_TOKEN_EXPIRE_HOURS` | `24` | Validade do token de guest |
| `GUEST_DAILY_MESSAGES` | `10` | Cota diária por `gid` |
| `GUEST_RATE_PER_MIN` | `3` | Limite por minuto por `gid` |
| `GUEST_IP_DAILY_MESSAGES` | `30` | Cota diária por IP |
| `GUEST_TOKENS_PER_IP_PER_HOUR` | `10` | Emissões de token de guest por IP/hora |
| `GUEST_MAX_MESSAGES_PER_REQUEST` | `20` | Máximo de mensagens por requisição (guest) |
| `GUEST_MAX_MESSAGE_CHARS` | `2000` | Máximo de caracteres por mensagem (guest) |
| `RATE_LIMIT_ADMIN_CHAT_PER_MIN` | `60` | Limite de chat do admin por minuto |
| `MAX_MESSAGES_PER_REQUEST` | `100` | Máximo de mensagens por requisição (admin) |
| `MAX_MESSAGE_CHARS` | `8000` | Máximo de caracteres por mensagem (admin) |
| `ALLOW_CLIENT_SYSTEM_MESSAGES` | `false` | Permite `system` do admin |
| `ALLOW_SELF_REGISTRATION` | `false` | Habilita `POST /auth/register` |
| `REGISTRATION_DAILY_MESSAGES` | `100` | Cota diária padrão para contas cadastradas |
| `REGISTRATION_RATE_PER_MIN` | `10` | Limite por minuto de contas cadastradas |
| `REGISTRATION_MAX_ATTEMPTS_PER_IP_PER_HOUR` | `5` | Limite de tentativas de cadastro por IP |
| `KEY_ENCRYPTION_SECRET` | vazio | Segredo de 32+ caracteres, obrigatório em produção para BYOK |
| `PERSONALIZATION_MAX_MEMORIES` | `20` | Máximo de memórias por requisição |
| `PERSONALIZATION_MAX_MEMORY_CHARS` | `4000` | Máximo de caracteres por memória |
| `PERSONALIZATION_MAX_CHARS` | `6000` | Orçamento total de personalização |
| `DEFAULT_SYSTEM_PROMPT` | vazio | Prompt de sistema padrão |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Lockout de login |
| `MAX_REQUEST_BODY_BYTES` | `2097152` | Teto do corpo (`413`) |
| `TRUSTED_PROXIES` | vazio | CIDRs de proxies confiáveis |
| `CORS_ORIGINS` | `http://localhost:5173` | Origens permitidas |
| `UVICORN_WORKERS` | `1` | Processos; `> 1` exige `REDIS_URL` |
| `SHUTDOWN_TIMEOUT_SECONDS` | `30` | Prazo do graceful shutdown |
| `AI_PROVIDER` | `ollama` | Compatibilidade com configuração legada do provedor Ollama |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.1` | Endpoint e modelo local |
| `AI_TIMEOUT_SECONDS` | `120` | Timeout de transporte |
| `AI_STREAM_IDLE_TIMEOUT_SECONDS` | `60` | Inatividade no streaming |
| `AI_MAX_OUTPUT_TOKENS` | `1024` | Teto de tokens de saída |
| `AI_MAX_OUTPUT_SECONDS` | `300` | Teto de duração da geração |
| `AI_ALLOWED_MODELS` | vazio | Allowlist de modelos |
| `MODELS_CACHE_SECONDS` | `10` | TTL do catálogo |
| `REDIS_URL` | vazio | Obrigatório com mais de um worker |

### Variáveis de provedores e persistência

As variáveis abaixo configuram o gateway multi-provedor, o SQLite e as rotas de
compatibilidade em execução. Consulte `.env.example` e os documentos específicos
para os valores completos.

| Grupo | Variáveis |
|---|---|
| Gateway | `AI_DEFAULT_MODEL`, `AI_MODEL_ALIASES`, `AI_ALLOWED_MODELS`, `AI_FALLBACK_CHAIN`, `AI_ENABLED_PROVIDERS`, `AI_MAX_RETRIES` |
| Provedores | `OLLAMA_CLOUD_*`, `OPENAI_*`, `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_DEFAULT_MODEL`, `ANTHROPIC_*`, `GOOGLE_*`, `OPENAI_COMPAT_*` |
| SQLite | `DATABASE_URL`, `SQLITE_*`, `USERS_CACHE_*`, `ALLOW_TEST_USERS`, `TEST_USER_*`, `DB_*` |
| Compatibilidade | `COMPAT_OPENAI_ENABLED`, `COMPAT_ANTHROPIC_ENABLED`, `COMPAT_OLLAMA_ENABLED`, `COMPAT_API_KEYS`, `COMPAT_API_KEY_RATE_PER_MIN`, `COMPAT_ALLOW_ANONYMOUS`, `COMPAT_OLLAMA_ALLOW_LOCALHOST` |

Para usar NVIDIA NIM com uma chave global do servidor, configure as variáveis no
`.env`. O endpoint hospedado padrão é `https://integrate.api.nvidia.com/v1`;
`AI_ENABLED_PROVIDERS` deve incluir `nvidia` (ou ficar vazio para autodetecção).

```dotenv
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_DEFAULT_MODEL=meta/llama-3.1-8b-instruct
AI_ENABLED_PROVIDERS=ollama,nvidia
AI_DEFAULT_MODEL=nvidia/meta/llama-3.1-8b-instruct
```

O catálogo NVIDIA da API é filtrado para mostrar os modelos documentados para
chat completions. Para chaves BYOK, a URL base pode ser enviada junto da chave;
se omitida, a API usa `NVIDIA_BASE_URL`. URLs personalizadas passam por
validação HTTPS e DNS público para usuários comuns.

## Execução

Local:

```bash
make run
# equivalente a:
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8000 \
  --timeout-graceful-shutdown 30
```

Produção (mesma entrada usada no contêiner):

```bash
.venv/bin/python -m app
```

### Endpoints nativos atuais

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/v1/auth/login` | Login do administrador (JWT) |
| POST | `/api/v1/auth/register` | Cadastro público, quando habilitado |
| POST | `/api/v1/auth/guest` | Emite token de convidado (`gid` + `usage`) |
| GET | `/api/v1/usage` | Limites e uso restante do chamador |
| POST | `/api/v1/chat` | Chat (JSON ou SSE) |
| GET | `/api/v1/models` | Catálogo multi-provedor (inclui origem da chave); NVIDIA lista apenas modelos LLM documentados para chat |
| GET | `/api/v1/me/providers` | Lista chaves BYOK mascaradas |
| PUT | `/api/v1/me/providers/{provider}` | Salva/atualiza uma chave BYOK; NVIDIA aceita URL base opcional |
| DELETE | `/api/v1/me/providers/{provider}` | Remove uma chave BYOK |
| POST | `/api/v1/me/providers/{provider}/test` | Testa uma chave salva |
| GET | `/api/v1/admin/stats` | Estatísticas em memória (admin) |
| GET | `/api/v1/admin/ai/status` | Status do provedor (admin) |
| GET | `/api/v1/admin/guests` | Guests ativos (admin) |
| POST | `/api/v1/admin/usage/reset` | Zera cota de `gid`/IP (admin) |
| GET | `/health` | Saúde da API |
| GET | `/health/ready` | Prontidão (inclui provedor) |
| GET | `/docs` / `/redoc` | OpenAPI |

### Outros endpoints nativos e compatibilidade

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/v1/admin/users` | Cria usuário de teste |
| POST | `/api/v1/admin/users/bulk` | Cria usuários em lote |
| GET | `/api/v1/admin/users` | Lista usuários com cursor/filtros |
| PATCH | `/api/v1/admin/users/{id}` | Atualiza usuário de teste |
| POST | `/api/v1/admin/users/{id}/reset-password` | Redefine senha |
| DELETE | `/api/v1/admin/users/{id}` | Exclui usuário de teste |
| POST | `/api/v1/admin/users/cleanup` | Remove expirados |
| GET | `/api/v1/admin/db/status` | Status/latência do SQLite |
| POST | `/v1/chat/completions` | Compatível OpenAI |
| GET | `/v1/models` | Compatível OpenAI |
| POST | `/v1/messages` | Compatível Anthropic (Claude Code) |
| POST | `/v1/messages/count_tokens` | Contagem de tokens (Anthropic) |
| POST | `/api/chat` | Compatível Ollama (NDJSON) |
| GET | `/api/tags` | Compatível Ollama |
| POST | `/api/show` | Compatível Ollama |
| GET | `/api/ps` | Compatível Ollama |

Personalização opcional no chat (`display_name` e memórias ativas) é validada e
transformada em contexto de sistema pela API. O uso BYOK não reduz a cota diária,
mas continua sujeito ao limite por minuto. Chaves nunca são devolvidas em texto
plano; configure `KEY_ENCRYPTION_SECRET` antes de habilitar esse recurso em
produção.

Todas as respostas trazem `X-Request-ID`; erros nativos seguem
`application/problem+json` (SRS §3.8). As rotas de compatibilidade usam o
envelope da API externa correspondente.

## Docker

A imagem é multi-stage, roda como usuário não-root (`10001:10001`), declara
`HEALTHCHECK` em `/health` e sobe **um** processo (`UVICORN_WORKERS=1`), já que
os contadores são em memória (RNF-POR-03).

```bash
make docker-build        # docker build -t nexa-api .
make docker-up           # API apenas, usando um Ollama no host
make docker-up-ollama    # API + Ollama no perfil opcional "ollama"
make docker-down
```

O compose lê `.env` (com `format: raw`, para preservar os `$` do hash argon2id;
requer Docker Compose ≥ 2.24) e aplica `read_only`, `tmpfs /tmp`,
`no-new-privileges` e `stop_grace_period: 45s`. Como o `.env` aponta
`OLLAMA_URL` para `localhost`, o compose usa `DOCKER_OLLAMA_URL`:
por padrão `http://host.docker.internal:11434` (Ollama no host), e o alvo
`docker-up-ollama` passa `http://ollama:11434`.

```bash
docker compose --profile ollama exec ollama ollama pull llama3.1
docker compose --profile ollama exec ollama ollama list
```

### SQLite e credenciais BYOK

O SQLite guarda o ADMIN permanente, usuários, chaves de provedor criptografadas
e o controle de migrações — nenhum conteúdo de chat é persistido.

- o arquivo padrão é `./data/nexa.db`, com diretório `0700` e arquivo `0600`;
- o compose monta o volume nomeado `nexa-data` em `/app/data`, o único caminho
gravável da imagem (`read_only: true`);
- o ADMIN é semeado da configuração e protegido por triggers;
- o backup é feito antes de cada migração em `./data/backups`.

```bash
# backup manual consistente (desenvolvimento local)
sqlite3 ./data/nexa.db ".backup './data/backups/nexa-$(date -u +%Y%m%dT%H%M%SZ).db'"

# backup manual consistente (Docker Compose)
docker compose exec api python -c "import sqlite3; sqlite3.connect('/app/data/nexa.db').execute(\"VACUUM INTO '/app/data/backups/manual.db'\")"

# benchmark do SQLite (10 mil usuários) e metas de p95
make bench
```

### Shutdown gracioso

Ao receber `SIGTERM` (ex.: `docker stop`), o uvicorn para de aceitar conexões e
aguarda as requisições em andamento — inclusive streams SSE — até
`SHUTDOWN_TIMEOUT_SECONDS`. Streams em curso recebem o evento final `done`
antes do encerramento; ao estourar o prazo, as conexões restantes são cortadas.
No compose, mantenha `stop_grace_period` maior que esse valor.

## Exemplos com `curl` (v1.1.0)

Login do administrador:

```bash
curl -sS -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}'
```

Token de convidado:

```bash
curl -sS -X POST http://localhost:8000/api/v1/auth/guest
```

Chat em JSON (`stream=false`):

```bash
TOKEN=...   # access_token retornado por /auth/login ou /auth/guest
curl -sS -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Explique SSE em uma frase."}],"stream":false}'
```

Catálogo e escolha de modelo:

```bash
curl -sS http://localhost:8000/api/v1/models \
  -H "Authorization: Bearer $TOKEN"

curl -sS 'http://localhost:8000/api/v1/models?refresh=true' \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Olá!"}],"stream":false,"model":"llama3.1"}'
```

Chat em streaming (`curl -N` desativa o buffer):

```bash
curl -N -sS -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Conte até cinco."}],"stream":true}'
```

Uso e administração:

```bash
curl -sS http://localhost:8000/api/v1/usage \
  -H "Authorization: Bearer $TOKEN"

curl -sS http://localhost:8000/api/v1/admin/stats \
  -H "Authorization: Bearer $ADMIN_TOKEN"

curl -sS -X POST http://localhost:8000/api/v1/admin/usage/reset \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"scope":"gid","value":"<gid>"}'
```

## Compatibilidade com APIs externas (v2.0)

> OpenAI, Anthropic/Claude Code e Ollama disponíveis desde as F14–F16.

OpenAI (requer `COMPAT_API_KEYS` configurado):

```bash
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $NEXA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Olá!"}],"stream":false}'
```

Anthropic (Claude Code):

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_AUTH_TOKEN=$NEXA_API_KEY
export ANTHROPIC_API_KEY=$NEXA_API_KEY
```

Ollama:

```bash
curl -sS -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $NEXA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"ollama/llama3.1","messages":[{"role":"user","content":"Olá!"}],"stream":false}'
```

## Qualidade

```bash
make lint   # ruff check + ruff format --check
make type   # mypy strict
make test   # pytest
make cov    # pytest + cobertura (mínimo 80%, gera coverage.xml)
make check  # tudo acima
```

O contrato do OpenAPI é versionado em `tests/snapshots/openapi.json`. Após uma
mudança intencional, regenere e revise o diff:

```bash
UPDATE_OPENAPI=1 pytest tests/integration/test_openapi_contract.py
```

## Estrutura

```text
app/
├── api/          # rotas HTTP nativas
├── ai/           # provedores, registry, router, capacidades (v2)
├── compat/       # traduções OpenAI/Anthropic/Ollama (v2)
├── db/           # SQLite, migrações e repositório (v2)
├── core/         # auth, usuários, cotas, erros, logging, middlewares
├── schemas/      # modelos Pydantic de entrada/saída
├── config.py     # Settings (12-factor, fail-fast)
├── main.py       # create_app() — sem instância global
└── __main__.py   # entrada de produção com shutdown gracioso
scripts/          # hash de senha, benchmark do SQLite (v2)
tests/
├── unit/
├── integration/
├── contract/     # OpenAI/Anthropic/Ollama (v2)
├── performance/  # benchmarks do SQLite (v2)
└── e2e/          # Claude Code/SSE (v2)
```

## Estado de implementação

**Concluídas (v1.x):** F0 Fundação, F1 Autenticação, F2 Provedores de IA,
F3 Cotas, F4 Chat JSON, F5 Streaming SSE, F6 Administração, F7 Hardening,
F8 Observabilidade, F9 Empacotamento, F10 Conformidade e F11 Catálogo de modelos.

**Implementadas no código:** F12 Gateway multi-provedor, F13 SQLite de usuários,
F14 Compatibilidade OpenAI, F15 Compatibilidade Anthropic/Claude Code, F16
Compatibilidade Ollama e F18 Cadastro/personalização/BYOK.

**Pendentes antes do release:** F17 Hardening e aceite operacional; a versão do
pacote ainda é `1.1.0` e não foi criada/tagueada uma release `2.0.0`.

## Release

O histórico `v1.1.0` e sua rodada manual estão em
[`docs/aceitacao-manual-2026-10-28.md`](docs/aceitacao-manual-2026-10-28.md).
O checklist de release v2.0 permanece aberto em
[`docs/checklist-release.md`](docs/checklist-release.md); mudanças no código e
passagem da suíte, por si só, não equivalem a uma publicação.
