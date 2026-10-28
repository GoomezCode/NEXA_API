# Nexa API

Backend do chat de IA via web, em **Python/FastAPI**, sem banco de dados.

- **SRS:** [`docs/srs-api-chat-ia-v1.md`](docs/srs-api-chat-ia-v1.md) (v1.2)
- **Plano de implementação:** [`docs/plano-implementacao-api-chat-ia-v1.md`](docs/plano-implementacao-api-chat-ia-v1.md) (v1.2)
- **Matriz CA → teste:** [`docs/matriz-ca-testes.md`](docs/matriz-ca-testes.md)
- **Checklist de release:** [`docs/checklist-release.md`](docs/checklist-release.md) (v1.1.0)

## Requisitos

- Python 3.11+ (o CI usa 3.12)
- `make` (opcional)
- Docker + Docker Compose (opcional, para a imagem de produção)

## Configuração

```bash
cp .env.example .env   # ajuste ADMIN_PASSWORD_HASH e JWT_SECRET
make setup             # cria .venv e instala dependências
```

Gere o hash da senha do administrador:

```bash
.venv/bin/python scripts/hash_password.py
```

Em produção (`ENVIRONMENT=production`), `JWT_SECRET` precisa ter ao menos 32 bytes e
não pode ser um valor de exemplo. `UVICORN_WORKERS > 1` exige `REDIS_URL`.

### Variáveis principais

Todas vêm do ambiente ou de um `.env` na raiz (12-factor). O `.env.example`
lista o conjunto completo com os valores padrão.

| Variável | Padrão | Descrição |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` ou `production` (habilita HSTS e validação estrita do segredo). |
| `LOG_LEVEL` | `INFO` | Nível dos logs JSON. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | — | Credenciais do administrador (hash argon2id/bcrypt). Obrigatórias. |
| `JWT_SECRET` | — | Segredo de assinatura dos tokens. Obrigatório; ≥ 32 bytes em produção. |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | `60` | Validade do token de admin. |
| `GUEST_TOKEN_EXPIRE_HOURS` | `24` | Validade do token de guest. |
| `GUEST_DAILY_MESSAGES` | `10` | Cota diária por `gid`. |
| `GUEST_RATE_PER_MIN` | `3` | Limite de mensagens por minuto por `gid`. |
| `GUEST_IP_DAILY_MESSAGES` | `30` | Cota diária por IP (aplicada junto com a do `gid`). |
| `GUEST_TOKENS_PER_IP_PER_HOUR` | `10` | Emissões de token de guest por IP/hora. |
| `GUEST_MAX_MESSAGES_PER_REQUEST` | `20` | Máximo de mensagens por requisição de guest. |
| `GUEST_MAX_MESSAGE_CHARS` | `2000` | Máximo de caracteres por mensagem de guest. |
| `RATE_LIMIT_ADMIN_CHAT_PER_MIN` | `60` | Limite de chat do admin por minuto. |
| `MAX_MESSAGES_PER_REQUEST` | `100` | Máximo de mensagens por requisição do admin. |
| `MAX_MESSAGE_CHARS` | `8000` | Máximo de caracteres por mensagem do admin. |
| `ALLOW_CLIENT_SYSTEM_MESSAGES` | `false` | Permite que o admin envie mensagens `system`. |
| `DEFAULT_SYSTEM_PROMPT` | vazio | Prompt de sistema aplicado quando o cliente não envia um. |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Lockout de login por IP. |
| `MAX_REQUEST_BODY_BYTES` | `2097152` | Teto do corpo da requisição (`413` acima disso). |
| `TRUSTED_PROXIES` | vazio | CIDRs de proxies cujo `X-Forwarded-For` é confiável. |
| `CORS_ORIGINS` | `http://localhost:5173` | Origens permitidas. |
| `UVICORN_WORKERS` | `1` | Processos do servidor; `> 1` exige `REDIS_URL`. |
| `SHUTDOWN_TIMEOUT_SECONDS` | `30` | Prazo máximo do graceful shutdown (SIGTERM). |
| `AI_PROVIDER` | `ollama` | Provedor de IA (`ollama`; `cloud` não está implementado). |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `llama3.1` | Endpoint e modelo do Ollama. |
| `AI_TIMEOUT_SECONDS` | `120` | Timeout de transporte de uma chamada ao provedor. |
| `AI_STREAM_IDLE_TIMEOUT_SECONDS` | `60` | Timeout de inatividade durante o streaming. |
| `AI_MAX_OUTPUT_TOKENS` | `1024` | Teto de tokens de saída. |
| `AI_MAX_OUTPUT_SECONDS` | `300` | Teto de duração da geração (`finish_reason=timeout`). |
| `AI_ALLOWED_MODELS` | vazio | Modelos que o cliente pode escolher (lista separada por vírgulas); vazio = todos os instalados. O modelo padrão é sempre permitido. |
| `MODELS_CACHE_SECONDS` | `10` | TTL do cache do catálogo de modelos; `0` desativa. |
| `REDIS_URL` | vazio | Obrigatório apenas com mais de um worker. |

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

Endpoints disponíveis na fase atual:

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/v1/auth/login` | Login do administrador (JWT) |
| POST | `/api/v1/auth/guest` | Emite token de convidado (`gid` + `usage` inicial) |
| GET | `/api/v1/usage` | Limites e uso restante do chamador |
| POST | `/api/v1/chat` | Envia o contexto e recebe a resposta do modelo (JSON ou SSE) |
| GET | `/api/v1/models` | Lista os modelos instalados no Ollama e o modelo padrão |
| GET | `/api/v1/admin/stats` | Estatísticas de uso em memória (somente admin) |
| GET | `/api/v1/admin/ai/status` | Status do provedor de IA (somente admin) |
| GET | `/api/v1/admin/guests` | Guests ativos e uso por IP (somente admin) |
| POST | `/api/v1/admin/usage/reset` | Zera a cota de um `gid` ou IP (somente admin) |
| GET | `/health` | Saúde da API (`status`, `version`, `request_id`) |
| GET | `/health/ready` | Prontidão (inclui provedor de IA) |
| GET | `/docs` | OpenAPI (Swagger UI) |
| GET | `/redoc` | OpenAPI (ReDoc) |

Todas as respostas trazem `X-Request-ID`; erros seguem o padrão
`application/problem+json` (SRS §3.8). Corpos de requisição devem ser
`application/json` (charset opcional); outros tipos recebem
`415 unsupported_media_type`.

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
`OLLAMA_URL` para `localhost` (uso local), o compose usa `DOCKER_OLLAMA_URL`:
por padrão `http://host.docker.internal:11434` (Ollama no host), e o alvo
`docker-up-ollama` passa `http://ollama:11434`. Com o perfil, baixe o modelo
uma vez:

```bash
docker compose --profile ollama exec ollama ollama pull llama3.1
docker compose --profile ollama exec ollama ollama list
```

Para um Ollama externo: `DOCKER_OLLAMA_URL=https://ollama.exemplo.com make docker-up`.

### Shutdown gracioso

Ao receber `SIGTERM` (ex.: `docker stop`), o uvicorn para de aceitar conexões e
aguarda as requisições em andamento — inclusive streams SSE — até
`SHUTDOWN_TIMEOUT_SECONDS`. Streams em curso recebem o evento final `done`
antes do encerramento; ao estourar o prazo, as conexões restantes são cortadas.
No compose, mantenha `stop_grace_period` maior que esse valor.

## Exemplos com `curl`

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

Catálogo de modelos e escolha por requisição:

```bash
curl -sS http://localhost:8000/api/v1/models \
  -H "Authorization: Bearer $TOKEN"

# `refresh=true` ignora o cache curto do catálogo.
curl -sS 'http://localhost:8000/api/v1/models?refresh=true' \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Olá!"}],"stream":false,"model":"llama3.1"}'
```

Sem `model`, usa-se `OLLAMA_MODEL`. Nome sem tag resolve para `:latest`; o
modelo precisa estar instalado e, se `AI_ALLOWED_MODELS` estiver definido, na
allowlist. Escolha inválida retorna `422` sem consumir cota.

Chat em streaming (`curl -N` desativa o buffer):

```bash
curl -N -sS -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Conte até cinco."}],"stream":true}'
```

O fluxo emite `data: {"content":"..."}` por pedaço, `event: done` com
`finish_reason` e `usage`, e `event: error` se a falha ocorrer após o início.
Um comentário `: ping` é enviado a cada 15 s para manter a conexão viva.

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
├── api/          # rotas HTTP
├── ai/           # provedores (Ollama, cloud, fake) e factory
├── core/         # config auxiliar, erros, logging, middlewares, IP, clock
├── schemas/      # modelos Pydantic de entrada/saída
├── config.py     # Settings (12-factor, fail-fast)
├── main.py       # create_app() — sem instância global
└── __main__.py   # entrada de produção com shutdown gracioso
scripts/          # utilitários (hash de senha)
tests/
├── unit/         # configuração, IP, erros, logging, middlewares, clock
└── integration/  # rotas ASGI com TestClient
```

## Fases

A implementação segue o plano: **F0 Fundação**, **F1 Autenticação**,
**F2 Provedores de IA**, **F3 Cotas e limites**, **F4 Chat JSON**,
**F5 Streaming SSE**, **F6 Administração**, **F7 Hardening**,
**F8 Observabilidade**, **F9 Empacotamento**, **F10 Conformidade** e
**F11 Catálogo de modelos** (concluídas).

## Release

A entrega `v1.1.0` (API reporta `1.1.0`) acrescenta o catálogo `GET /models` e a
escolha de modelo por requisição de chat (SRS v1.2), com os critérios
CA-01..CA-25 rastreados em [`docs/matriz-ca-testes.md`](docs/matriz-ca-testes.md)
e a rodada manual com Ollama real em
[`docs/aceitacao-manual-2026-10-28.md`](docs/aceitacao-manual-2026-10-28.md).
Pendências e limitações conhecidas estão em
[`docs/checklist-release.md`](docs/checklist-release.md). A entrega `v1.0.0`
permanece como marco anterior (SRS v1.1).
