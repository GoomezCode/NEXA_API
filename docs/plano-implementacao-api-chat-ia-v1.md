# Plano de Implementação — API do Chat de IA

## Backend Python/FastAPI conforme SRS v2.0

| Campo | Valor |
|---|---|
| **Projeto** | Chat de IA via Web: Backend (API) |
| **Documento** | Plano de Implementação |
| **Versão** | 2.0 |
| **Data** | 29/10/2026 |
| **Documento de referência** | `docs/srs-api-chat-ia-v1.md` (SRS v2.0) |
| **Escopo** | Gateway multi-provedor, compatibilidade OpenAI/Anthropic/Ollama e SQLite de usuários |
| **Público-alvo** | Desenvolvedores, QA e responsável técnico do projeto |
| **Estado** | **Concluído** - F17 hardening, performance e release `v2.0.0` finalizado. |

### Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 2.0 | 29/10/2026 | Fases F12–F17: gateway multi-provedor, SQLite de usuários com ADMIN inviolável, compatibilidade OpenAI, Anthropic (Claude Code) e Ollama, performance e release `v2.0.0` |
| 1.2 | 28/10/2026 | Fase F11: catálogo `GET /models`, seleção opcional de modelo no chat, allowlist `AI_ALLOWED_MODELS`, cache `MODELS_CACHE_SECONDS` e release `v1.1.0` |
| 1.1 | 28/10/2026 | Reordenação das fases (cotas antes do chat), backlog atômico com dependências e caminho crítico; versões mínimas e alternativas de stack; cronograma datado com marcos e buffer; ajuste dos gates de CA e da matriz de testes |
| 1.0 | 28/10/2026 | Primeira versão do plano, derivada do SRS v1.1 |

---

## Sumário

1. [Objetivo e escopo](#1-objetivo-e-escopo)
2. [Estratégia de execução](#2-estratégia-de-execução)
3. [Stack e decisões técnicas](#3-stack-e-decisões-técnicas)
4. [Arquitetura](#4-arquitetura)
5. [Contratos e modelos](#5-contratos-e-modelos)
6. [Fases de implementação](#6-fases-de-implementação)
7. [Estratégia de testes](#7-estratégia-de-testes)
8. [Qualidade e CI](#8-qualidade-e-ci)
9. [Configuração e ambientes](#9-configuração-e-ambientes)
10. [Riscos e mitigação](#10-riscos-e-mitigação)
11. [Cronograma](#11-cronograma)
12. [Definition of Done e checklist de aceitação](#12-definition-of-done-e-checklist-de-aceitação)
13. [Anexos](#13-anexos)

---

## 1. Objetivo e escopo

### 1.1 O que será construído

Uma **API REST em Python/FastAPI** que evolui a v1.1.0 para:

- um **gateway multi-provedor** com adapters para Ollama local, Ollama Cloud, OpenAI, Anthropic, Google Gemini e provedores OpenAI-compatible;
- **roteamento por Model ID** (`provider/model`), aliases, allowlist com globs, validação de capacidades e fallback controlado;
- **camadas de compatibilidade** com OpenAI (`/v1`), Anthropic/Claude Code (`/v1/messages`) e Ollama (`/api`);
- **SQLite exclusivo para usuários**, com ADMIN permanente/inviolável e usuários de teste descartáveis;
- **performance extrema** nas consultas de usuário: WAL, índices, cache, pool de leitura e escrita serializada;
- manutenção do **chat efêmero**, das cotas em memória/Redis e dos endpoints administrativos existentes.

### 1.2 Fora de escopo (herdado do SRS §5.1)

- Frontend React, histórico de conversas, feedback, e-mail, refresh token, auditoria persistente, billing, MFA, upload/RAG e embeddings.
- Tabela de API keys no banco; as chaves de compatibilidade ficam em `COMPAT_API_KEYS`.
- Bancos além do SQLite.
- Implementação de um SDK próprio; os adapters usam `httpx`.

### 1.3 Resultado esperado

API executável localmente e em contêiner, com:

- todos os endpoints nativos e de compatibilidade documentados;
- **CA-01 a CA-47** cobertos por teste automatizado;
- cobertura ≥ **80%** em `app/api`, `app/core`, `app/ai`, `app/compat` e `app/db`;
- metas de performance do SQLite medidas em benchmark;
- pipeline de lint, tipagem e testes verde;
- release `v2.0.0`.

---

## 2. Estratégia de execução

### 2.1 Abordagem

**Fatias verticais incrementais**, cada uma entregando um caminho utilizável de ponta a ponta, com testes no mesmo PR:

1. **Gateway** — capacidades, adapters, registry/router, catálogo e fallback.
2. **Banco** — conexão, migrações, repositório, admin protegido, usuários de teste, cache e performance.
3. **Compat OpenAI** — contrato, tradução, streaming e erros.
4. **Compat Anthropic** — Messages API, tools, streaming e `count_tokens` (Claude Code).
5. **Compat Ollama** — `/api/chat`, `/api/tags`, `/api/show` e NDJSON.
6. **Hardening/conformidade** — segurança, benchmarks, OpenAPI, docs e release.

> **Por que o gateway antes do banco:** as rotas de compatibilidade dependem do roteamento e das capacidades. Com o gateway estável, o banco entra sem retrabalho nas traduções.

> **Por que o banco antes da compatibilidade:** a autenticação de usuários de teste e o cache precisam estar prontos para que as rotas de compatibilidade apliquem cotas e identidade corretamente.

### 2.2 Princípios

- **SQLite só para usuários**: nenhuma tabela de conversa, mensagem, feedback ou auditoria.
- **ADMIN inviolável**: configuração como fonte de verdade, espelho no banco, triggers e guardas.
- **Um processo** enquanto os contadores forem em memória (`UVICORN_WORKERS=1`).
- **Fronteiras explícitas**: rotas não conhecem `httpx`/provedores; só `ProviderRouter`/`AIProvider`. Rotas não conhecem SQL; só `UserRepository`.
- **Compatibilidade é tradução**: as rotas `/v1` e `/api` não duplicam regra de negócio.
- **Erros como contrato**: cada superfície tem seu envelope, com `code` estável.
- **Conteúdo nunca em log**: o pipeline só emite metadados.
- **Tempo injetável**: `Clock` e janelas testáveis sem `sleep`.
- **Fail-fast**: configuração inválida ou admin ausente impedem o boot.

### 2.3 Fluxo de trabalho

- Branch `main` protegida; PRs pequenos por tarefa.
- *Conventional Commits*: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.
- Nenhum PR sem teste correspondente.
- Revisão obrigatória em: `core/security.py`, `core/users.py`, `db/repository.py`, `ai/router.py`, `compat/*` e `api/chat.py`.

---

## 3. Stack e decisões técnicas

| Área | Escolha | Motivo |
|---|---|---|
| Runtime | **Python 3.12** (mínimo 3.11) | SRS §2.4; tipagem moderna e `asyncio` |
| Framework | **FastAPI** + **Uvicorn** (1 worker) | RP-01, RP-05 |
| Validação | **Pydantic v2** + `pydantic-settings` | RNF-SEG-06, RNF-POR-02 |
| JWT | **PyJWT** | RP-03 |
| Hash | **argon2-cffi** (`argon2id`) + bcrypt (legado) | RNF-SEG-01 |
| HTTP cliente | **httpx** (async) | RP-04, RNF-DES-03 |
| Banco | **SQLite** via **aiosqlite** + SQL parametrizado | RP-02, RP-10, RNF-DES-03 |
| Migrações | SQL versionado próprio (`schema_migrations`) | RNF-MAN-10; sem ORM/Alembic |
| Tokenização (opcional) | `tiktoken` para estimativa; fallback heurístico | `count_tokens` da Anthropic e métricas |
| Testes | **pytest**, `pytest-asyncio`, `httpx.ASGITransport`, `respx` | Sem porta real; mock de provedores |
| Cobertura | **coverage.py** com gate de 80% | RNF-MAN-06, CA-18 |
| Lint/format | **Ruff** | RNF-MAN-07 |
| Tipagem | **mypy** strict | RNF-MAN-07 |
| Dependências | **uv** + `requirements.txt` compilado | RNF-MAN-08 |
| Logs | **structlog** (JSON) | RNF-OBS-01 |
| Contêiner | **Docker** multi-stage + **docker-compose** (API + Ollama opcional + volume SQLite) | RNF-POR-01/04 |
| CI | **GitHub Actions** | RNF-MAN-07 |

Dependências principais adicionais (pinadas no `requirements.txt`):

```
aiosqlite
tiktoken          # opcional; usado apenas para estimativa de tokens
```

### 3.2 Versões mínimas e alternativas avaliadas

| Componente | Mínimo | Alternativa descartada | Motivo da escolha |
|---|---|---|---|
| Python | 3.12 (aceita 3.11) | — | SRS §2.4 |
| FastAPI | 0.115 | Flask | Async nativo, OpenAPI automático |
| Pydantic | 2.9 | dataclasses | Validação declarativa e `extra="forbid"` |
| Uvicorn | 0.30 | Hypercorn | Simplicidade e `--workers 1` |
| PyJWT | 2.9 | `python-jose` | Mantido, menor superfície |
| argon2-cffi | 23 | `passlib[argon2]` | `passlib` sem manutenção ativa |
| httpx | 0.27 | `aiohttp` | Cliente único para runtime e testes |
| aiosqlite | 0.20 | SQLAlchemy | SQL direto é mais leve e previsível para poucas tabelas |
| structlog | 24 | `logging` + formatador | Contexto por requisição e redaction |
| Rate limiter | próprio | `slowapi`/`fastapi-limiter` | Janela fixa e deslizante, refund e O(1) |
| pytest | 8 | unittest | Fixtures e parametrização |
| respx | 0.21 | `responses` | Funciona com `httpx` async |
| Ruff | 0.7 | flake8 + black + isort | Uma ferramenta só |
| mypy | 1.11 | pyright | Já previsto no SRS |

**Versionamento:** a tabela fixa o mínimo aceitável; as versões exatas ficam no `requirements.txt` compilado e no lock.

---

## 4. Arquitetura

### 4.1 Visão de camadas

```
HTTP (FastAPI)
  │  middlewares: request_id, body limit, content-type, security headers, CORS, logging
  ▼
api/ (rotas nativas)      compat/ (rotas OpenAI/Anthropic/Ollama)
  │                              │
  ├── schemas/ (Pydantic)        ├── translate.py (contratos externos ↔ internos)
  ├── core/errors.py             └── compat/errors.py (envelopes externos)
  │
  ├── core/auth.py + core/users.py (AuthService/UserService)
  ├── core/api_keys.py            (API keys NEXA)
  ├── core/rate_limit.py          (login, token, minuto, API key)
  ├── core/usage_store.py         (gid/uid + IP)
  ├── core/stats.py               (métricas em memória)
  ├── ai/router.py ── ai/registry.py ── ai/providers/*.py
  └── db/repository.py ── db/connection.py ── db/migrations/*.sql
```

### 4.2 Estrutura de diretórios

```
NEXA_API/
├── app/
│   ├── __init__.py
│   ├── main.py                       # create_app(), lifespan, routers, middleware
│   ├── config.py                     # Settings + validação fail-fast
│   ├── api/
│   │   ├── deps.py                   # settings, usuário, store, router, api key
│   │   ├── auth.py                   # login admin/usuário + guest
│   │   ├── chat.py                   # POST /chat (JSON + SSE)
│   │   ├── models.py                 # catálogo agregado
│   │   ├── usage.py                  # GET /usage
│   │   ├── users.py                  # /admin/users*
│   │   ├── admin.py                  # stats, ai/status, guests, reset, db/status
│   │   └── system.py                 # /health, /health/ready, /health/db
│   ├── ai/
│   │   ├── base.py                   # AIProvider, Capabilities, resultados
│   │   ├── registry.py               # registry/factory de adapters
│   │   ├── router.py                 # ProviderRouter (Model ID, aliases, fallback)
│   │   ├── capabilities.py           # matriz e validação de capacidades
│   │   ├── errors.py                 # normalização de erros
│   │   └── providers/
│   │       ├── ollama.py             # local + cloud (parametrizado)
│   │       ├── openai.py
│   │       ├── anthropic.py
│   │       ├── google.py
│   │       ├── openai_compatible.py
│   │       └── fake.py
│   ├── compat/
│   │   ├── openai.py                 # /v1/chat/completions, /v1/models
│   │   ├── anthropic.py              # /v1/messages, count_tokens
│   │   ├── ollama.py                 # /api/chat, /api/tags, /api/show, /api/ps
│   │   ├── translate.py              # mapeamento de papéis, tools, finish, usage
│   │   └── errors.py                 # envelopes OpenAI/Anthropic/Ollama
│   ├── db/
│   │   ├── connection.py             # pool, PRAGMAs, transações
│   │   ├── migrations.py             # runner idempotente + backup
│   │   ├── migrations/
│   │   │   ├── 001_users.sql
│   │   │   └── 002_indexes.sql
│   │   ├── repository.py             # UserRepository (SQL parametrizado)
│   │   └── models.py                 # UserRow, UserCreate, UserUpdate
│   ├── core/
│   │   ├── security.py               # hash/verify, JWT
│   │   ├── auth.py                   # AuthService (admin + test users)
│   │   ├── users.py                  # UserService (regras e cache)
│   │   ├── api_keys.py               # autenticação por API key
│   │   ├── rate_limit.py             # janelas
│   │   ├── usage_store.py            # cotas gid/uid/IP
│   │   ├── stats.py                  # métricas
│   │   ├── errors.py                 # Problem Details + códigos
│   │   ├── logging.py                # JSON + redaction
│   │   ├── middleware.py             # request_id, body limit, headers
│   │   ├── ip.py                     # IP real com TRUSTED_PROXIES
│   │   └── clock.py                  # Clock injetável
│   ├── schemas/
│   │   ├── common.py                 # ErrorEnvelope, UsageInfo
│   │   ├── auth.py
│   │   ├── chat.py
│   │   ├── models.py
│   │   ├── admin.py
│   │   ├── users.py
│   │   └── system.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   ├── contract/                     # OpenAI/Anthropic/Ollama
│   ├── performance/                  # benchmarks do SQLite
│   └── e2e/
├── docs/
│   ├── srs-api-chat-ia-v1.md
│   ├── plano-implementacao-api-chat-ia-v1.md
│   ├── provedores-ia.md
│   ├── compatibilidade-apis-externas.md
│   ├── database-sqlite.md
│   ├── matriz-ca-testes.md
│   ├── checklist-release.md
│   └── aceitacao-manual-2026-10-28.md
├── scripts/
│   ├── hash_password.py
│   └── db_benchmark.py               # benchmark de leitura/escrita
├── .github/workflows/ci.yml
├── .env.example
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── README.md
```

### 4.3 Responsabilidades por módulo

| Módulo | Responsabilidade | Não faz |
|---|---|---|
| `api/*` | parsing, autorização, orquestração, resposta nativa | falar com provedores; executar SQL |
| `compat/*` | traduzir contratos externos e orquestrar o pipeline | duplicar cotas/regras; acessar banco |
| `schemas/*` | contrato nativo (`extra="forbid"`) | regra de negócio |
| `core/security.py` | hash/verify, emitir/validar JWT | decidir permissões |
| `core/auth.py` | autenticar admin/usuário, montar claims | conhecer SQL |
| `core/users.py` | regras de usuário, cache, proteção do admin | falar HTTP |
| `core/api_keys.py` | validar API key em tempo constante | emitir chaves |
| `core/rate_limit.py` | janelas de login/token/minuto/API key | cotas diárias |
| `core/usage_store.py` | cota diária `gid`/`uid`+IP, refund, reset | autenticação |
| `core/stats.py` | métricas em memória | persistência |
| `core/errors.py` | catálogo de códigos + Problem Details | envelope de compatibilidade |
| `ai/*` | registry, roteamento, capacidades, adapters | autenticação, cota, SSE |
| `db/*` | conexão, PRAGMAs, migrações, repositório | regra de negócio |
| `core/middleware.py` | request_id, limite de corpo, headers | regra de negócio |

### 4.4 Interfaces principais (assinaturas de referência)

```python
# ai/base.py
class Capabilities(BaseModel):
    chat: bool = True
    stream: bool = True
    tools: bool = False
    vision: bool = False
    json_mode: bool = False
    context_window: int | None = None

class AIProvider(Protocol):
    name: str
    model: str
    capabilities: Capabilities
    async def generate(self, messages: Sequence[ChatMessage], *, max_tokens: int,
                       max_seconds: float, request_id: str | None = None,
                       model: str | None = None, tools: Sequence[Tool] | None = None,
                       tool_choice: str | None = None) -> GenerationResult: ...
    def stream(self, messages: Sequence[ChatMessage], *, max_tokens: int,
               max_seconds: float, request_id: str | None = None,
               model: str | None = None, tools: Sequence[Tool] | None = None,
               tool_choice: str | None = None) -> AsyncIterator[GenerationChunk]: ...
    async def health(self) -> bool: ...
    async def list_models(self, *, request_id: str | None = None) -> Sequence[ModelInfo]: ...
    async def count_tokens(self, messages: Sequence[ChatMessage], *,
                           model: str | None = None) -> int | None: ...
```

```python
# ai/router.py
@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str              # nome no provedor
    model_id: str           # provider/model canônico
    capabilities: Capabilities

class ProviderRouter(Protocol):
    async def resolve(self, model_id: str | None, *, surface: str) -> ResolvedModel: ...
    async def catalog(self, *, refresh: bool = False) -> ModelsResponse: ...
    async def health(self) -> list[ProviderHealth]: ...
```

```python
# db/repository.py
class UserRepository(Protocol):
    async def get_by_username(self, username: str) -> UserRow | None: ...
    async def get_by_id(self, user_id: str) -> UserRow | None: ...
    async def create(self, data: UserCreate) -> UserRow: ...
    async def create_many(self, data: Sequence[UserCreate]) -> list[UserRow]: ...
    async def update(self, user_id: str, data: UserUpdate) -> UserRow: ...
    async def delete_test_user(self, user_id: str) -> bool: ...
    async def list(self, *, cursor: str | None, limit: int, filters: UserFilters) -> UserPage: ...
    async def cleanup_expired(self, now: datetime) -> int: ...
    async def ensure_admin(self, username: str, password_hash: str) -> UserRow: ...
    async def stats(self) -> DbStats: ...
```

```python
# core/usage_store.py
class UsageStore(Protocol):
    async def consume(self, *, subject: str, ip: str, now: datetime,
                      kind: Literal["guest", "user"]) -> UsageInfo: ...
    async def refund(self, *, subject: str, ip: str, now: datetime) -> None: ...
    async def snapshot(self, *, subject: str, ip: str, now: datetime) -> UsageInfo: ...
    async def reset(self, *, scope: Literal["gid", "uid", "ip"], value: str) -> bool: ...
    async def list_guests(self, *, now: datetime) -> GuestListResponse: ...
```

`UsageInfo` é o schema único de RD-08.

### 4.5 Pipeline do chat nativo

```
1. autenticar (JWT)                         → 401 com WWW-Authenticate
2. validar schema (Pydantic)                → 422 com errors[]
3. resolver modelo/alias                    → 422 model_not_found/model_not_allowed
4. validar capacidades                      → 422 unsupported_capability
5. aplicar limites de contexto              → 422
6. usage.consume(subject, ip)               → 429 (cota/rate limit)
7. montar contexto                          → DEFAULT_SYSTEM_PROMPT + mensagens
8. provider.stream/generate                 → 502/503/504 (antes do 1º trecho) | evento error
9. responder                                → JSON ou SSE (+ done) com model_id/provider_usage
10. métricas/logs                           → metadados apenas
11. falha antes do 1º trecho                → refund(subject) + refund(ip)
```

### 4.6 Pipeline das rotas de compatibilidade

```
1. autenticar API key (ou JWT)              → 401 no envelope externo
2. parse do contrato externo                → 400/422 no envelope externo
3. traduzir para ChatRequest interno        → tools, system, blocks, options
4. resolver modelo/alias + capacidades      → erro no envelope externo
5. usage.consume + rate limit da API key    → 429 no envelope externo
6. chamar provider via router               → 502/503/504 no envelope externo
7. traduzir resposta/stream                 → shape OpenAI/Anthropic/Ollama
8. métricas/logs                            → superfície + provider + model
```

### 4.7 Pipeline do banco no boot

```
1. ler DATABASE_URL                         → recusa se não for sqlite:///
2. garantir diretório e permissões          → 0700/0600
3. abrir conexão de escrita + pool          → PRAGMAs (WAL, busy_timeout, cache, mmap)
4. verificar integridade (quick_check)      → recusa se corrompido
5. backup (se DB_BACKUP_BEFORE_MIGRATION)   → ./data/backups/nexa-<ts>.db
6. aplicar migrações pendentes              → schema_migrations
7. ensure_admin(config)                     → upsert protegido
8. verificar admin pós-migração             → recusa/restaura backup se ausente
9. carregar cache de usuários               → opcional (lazy)
10. iniciar limpeza de expirados            → task de background
```

### 4.8 Middlewares e ordem

1. `RequestIDMiddleware` — aceita UUID válido ou gera; injeta no estado e no log.
2. `BodyLimitMiddleware` — lê `Content-Length` e conta bytes no stream; `413`.
3. `ContentTypeMiddleware` — `415` para corpos não-JSON nas rotas nativas.
4. `SecurityHeadersMiddleware` — `nosniff`, `DENY`, `no-referrer`, HSTS em produção.
5. `CORS` — origens, métodos, `expose_headers`.
6. `LoggingMiddleware` — duração, status, `request_id`, superfície, sem corpo.
7. Handlers de exceção — `HTTPException`, `RequestValidationError`, `QuotaExceeded`, `ProviderError`, `TimeoutError`, `DatabaseUnavailable`, `Exception`.

---

## 5. Contratos e modelos

| Modelo | Campos principais | Regras |
|---|---|---|
| `Settings` | todas as variáveis do SRS §3.1.4 | validação de tipo/faixa; recusa boot inválido |
| `LoginRequest` | `username`, `password` | `extra="forbid"` |
| `TokenResponse` | `access_token`, `token_type`, `expires_in`, `role`, `user_id` | `token_type="bearer"` |
| `GuestTokenResponse` | `TokenResponse` + `usage` | `role="guest"` |
| `Message` | `role` ∈ {system,user,assistant,tool}, `content`, `tool_calls?` | `content` não vazio após `strip` |
| `ChatRequest` | `messages`, `stream`, `model`, `tools`, `tool_choice` | última = `user`; sem parâmetros de geração; `extra="forbid"` |
| `ChatResponse` | `message`, `provider`, `model`, `model_id`, `finish_reason`, `provider_usage`, `usage` | `finish_reason` ∈ {stop,length,timeout,tool_use} |
| `ModelEntry` / `ModelsResponse` | `model_id`, `provider`, `name`, metadados, `capabilities`, `context_window`, `selectable` | `selectable` respeita allowlist |
| `UsageInfo` | 5 campos de RD-08 | admin: `daily_*` e `resets_at` nulos |
| `ErrorEnvelope` | `type`, `title`, `status`, `code`, `detail`, `request_id`, `errors?`, `resets_at?` | RFC 9457 estendida |
| `UserCreate` / `UserUpdate` / `UserRow` | `username`, `password?`, `email?`, `role`, `is_test`, `is_active`, `daily_message_limit`, `rate_per_min`, `expires_at` | admin nunca editável por API |
| `UserPage` | `users[]`, `next_cursor` | cursor opaco, limite ≤ 100 |
| `StatsResponse` | `since`, `chat_requests_total`, `guest_distinct`, `rate_limited_total`, `provider_errors_total`, `avg_response_seconds` | |
| `AIStatusResponse` | `providers[]` com `provider`, `default_model`, `available`, `checked_at` | nunca expõe chave |
| `DbStatusResponse` | `status`, `path`, `size_bytes`, `wal`, `migration_version`, `users_total`, `read_latency_ms_p95`, `write_latency_ms_p95` | sem dados sensíveis |
| `GuestListResponse` | `guests[]`, `ip_usage[]` | `gid`, `ip`, `daily_used`, `daily_limit`, `last_seen_at` |
| `UsageResetRequest` | `scope` ∈ {gid,uid,ip}, `value` | `404` se alvo não existir |
| `HealthResponse` | `status`, `version`, `request_id` | |
| `ReadyResponse` | `status`, `providers[]`, `database` | `503` se componente essencial indisponível |

**Claims JWT:** `sub` (admin username, `uid` ou `gid`), `role` (`admin`/`user`/`guest`), `iat`, `exp`. Sem `sid`/refresh.

**API key NEXA:** `nxa_` + segredo aleatório; comparação com `hmac.compare_digest`; log apenas do prefixo (8 caracteres) e fingerprint.

**Model ID:** `provider/model` (ex.: `openai/gpt-4o-mini`); aliases em `AI_MODEL_ALIASES`; allowlist com globs.

**Erros de compatibilidade:**

| Superfície | Envelope |
|---|---|
| OpenAI | `{"error": {"message", "type", "param", "code"}}` |
| Anthropic | `{"type": "error", "error": {"type", "message"}}` |
| Ollama | `{"error": "..."}` |

---

## 6. Fases de implementação

> Estimativas em dias de 1 desenvolvedor sênior, incluindo testes e revisão.

### Backlog atômico e dependências

Tamanhos: **S** ≈ 2–4 h, **M** ≈ 4–6 h, **L** ≈ 1 dia. Cada tarefa entra em um PR próprio (ou PR pequeno por par).

| ID | Tarefa | Tam. | Depende de | Artefato principal |
|---|---|---|---|---|
| F12-01 | `Capabilities`, `ModelInfo` v2 e tipos de tools | M | — | `ai/base.py`, `ai/capabilities.py` |
| F12-02 | Adapter OpenAI (`/chat/completions`, `/models`, SSE) | L | F12-01 | `ai/providers/openai.py` |
| F12-03 | Adapter Anthropic (`/v1/messages`, SSE, tools) | L | F12-01 | `ai/providers/anthropic.py` |
| F12-04 | Adapter Google Gemini (`generateContent`/stream) | L | F12-01 | `ai/providers/google.py` |
| F12-05 | Ollama Cloud (reuso do adapter Ollama + Bearer) | S | F12-01 | `ai/providers/ollama.py` |
| F12-06 | OpenAI-compatible genérico | M | F12-02 | `ai/providers/openai_compatible.py` |
| F12-07 | Registry + factory + `ProviderRouter` + aliases/allowlist | L | F12-02..06 | `ai/registry.py`, `ai/router.py` |
| F12-08 | Catálogo agregado + health por provedor | M | F12-07 | `api/models.py`, `api/system.py` |
| F12-09 | Validação de capacidades + fallback antes do 1º token | M | F12-07 | `ai/router.py`, `api/chat.py` |
| F12-10 | Config v2, depreciação de `AI_PROVIDER`/`CLOUD_*`, testes | M | F12-01..09 | `config.py`, `tests/` |
| F13-01 | Conexão SQLite + PRAGMAs + pool + transações | L | — | `db/connection.py` |
| F13-02 | Migrações `001_users.sql`/`002_indexes.sql` + runner | L | F13-01 | `db/migrations*` |
| F13-03 | `UserRepository` (CRUD, paginação, filtros) | L | F13-02 | `db/repository.py` |
| F13-04 | Seed do admin + triggers de proteção + verificação pós-migração | L | F13-03 | `db/repository.py`, SQL |
| F13-05 | `AuthService` para usuários de teste + cache + invalidação | M | F13-03 | `core/auth.py`, `core/users.py` |
| F13-06 | Endpoints `/admin/users*` (single, bulk, list, patch, reset, delete, cleanup) | L | F13-04/05 | `api/users.py`, `schemas/users.py` |
| F13-07 | `db/status`, backup e rotina de limpeza | M | F13-04 | `api/admin.py`, `db/migrations.py` |
| F13-08 | Benchmarks de leitura/escrita/listagem + `EXPLAIN QUERY PLAN` | M | F13-03 | `scripts/db_benchmark.py`, `tests/performance/` |
| F14-01 | Auth por API key + rate limit por chave | M | F13-05 | `core/api_keys.py`, `core/rate_limit.py` |
| F14-02 | `POST /v1/chat/completions` JSON | L | F12-07, F14-01 | `compat/openai.py` |
| F14-03 | Streaming OpenAI (`chat.completion.chunk` + `[DONE]`) | M | F14-02 | `compat/openai.py` |
| F14-04 | `GET /v1/models` + erros OpenAI | S | F14-01 | `compat/openai.py`, `compat/errors.py` |
| F14-05 | Testes de contrato OpenAI | M | F14-02..04 | `tests/contract/` |
| F15-01 | `POST /v1/messages` JSON + blocos de conteúdo | L | F12-03, F14-01 | `compat/anthropic.py` |
| F15-02 | Streaming Anthropic (eventos tipados) | L | F15-01 | `compat/anthropic.py` |
| F15-03 | Tools/`tool_use` + `count_tokens` | L | F15-01 | `compat/anthropic.py` |
| F15-04 | Testes de contrato Anthropic + Claude Code | M | F15-02/03 | `tests/contract/`, `tests/e2e/` |
| F16-01 | `POST /api/chat` NDJSON | M | F12-05, F14-01 | `compat/ollama.py` |
| F16-02 | `/api/tags`, `/api/show`, `/api/ps` | M | F16-01 | `compat/ollama.py` |
| F16-03 | Testes de contrato Ollama | S | F16-01/02 | `tests/contract/` |
| F17-01 | Hardening de API keys, logs e admin protegido | M | F14..F16 | `core/*`, `compat/*` |
| F17-02 | Benchmarks finais e ajuste de índices/PRAGMAs | M | F13-08 | `tests/performance/` |
| F17-03 | OpenAPI nativo + OpenAPI de compatibilidade | M | F14..F16 | `main.py`, snapshots |
| F17-04 | README, `.env.example`, SRS/plano/matriz/checklist | M | F17-01..03 | docs |
| F17-05 | Release `v2.0.0` | S | F17-04 | git tag |

**Caminho crítico:** `F12-01 → F12-02 → F12-07 → F13-01 → F13-02 → F13-03 → F13-04 → F13-06 → F14-01 → F14-02 → F15-01 → F15-02 → F17-01 → F17-05`.

### Fase 12 — Gateway multi-provedor (5 dias)

**Objetivo:** rotear `provider/model` para adapters reais, agregar catálogo e validar capacidades.

**Tarefas**

- [ ] `Capabilities` + `ModelInfo` v2 (`model_id`, `provider`, `capabilities`, `context_window`).
- [ ] Adapter OpenAI: `POST /chat/completions`, `GET /models`, SSE e mapeamento de erros.
- [ ] Adapter Anthropic: `POST /v1/messages`, SSE, blocos, tools e `count_tokens`.
- [ ] Adapter Google Gemini: `generateContent`/`streamGenerateContent`, safety e usage.
- [ ] Ollama Cloud: reuso do adapter Ollama com base URL `https://ollama.com` e Bearer.
- [ ] OpenAI-compatible genérico (Groq, OpenRouter, vLLM, LM Studio, Azure).
- [ ] Registry/factory + `ProviderRouter` com aliases, allowlist glob e resolução `:latest`.
- [ ] Catálogo agregado com cache e `?refresh=true`; health por provedor.
- [ ] Validação de capacidades e fallback antes do primeiro token.
- [ ] Config v2, depreciação de `AI_PROVIDER`/`CLOUD_*` e testes de boot.

**Testes:** `respx` por provedor (sucesso, 4xx/5xx, timeout, conexão recusada, JSON inválido, stream truncado); roteamento/aliases/allowlist; capacidades; fallback; catálogo; health agregado; configuração inválida.

**Gate:** CA-24..CA-30 verdes; `GET /api/v1/models` agrega pelo menos dois provedores no fake; boot falha com provedor habilitado sem credencial.

---

### Fase 13 — SQLite de usuários (4 dias)

**Objetivo:** persistir apenas usuários, com ADMIN inviolável, usuários de teste e consultas extremamente rápidas.

**Tarefas**

- [x] `db/connection.py`: `aiosqlite`, WAL, `synchronous=NORMAL`, `busy_timeout`, cache, mmap, `foreign_keys`, pool de leitura e escrita serializada.
- [x] Migrações `001_users.sql` e `002_indexes.sql`, com `schema_migrations`, backup e rollback em falha.
- [x] `UserRepository`: `get_by_username`, `get_by_id`, `create`, `create_many`, `update`, `delete_test_user`, `list` (cursor), `cleanup_expired`, `stats`.
- [x] Seed do admin com ID fixo, `is_protected=1`, triggers de proteção e verificação pós-migração.
- [x] `AuthService`: admin via config (fallback) + usuários de teste via repositório; cache TTL e invalidação.
- [x] Endpoints `/admin/users*`: criar, criar em lote, listar, consultar, atualizar, resetar senha, excluir e limpar expirados.
- [x] `GET /admin/db/status`, backup e rotina de limpeza agendada.
- [x] Benchmarks: 10 mil usuários, leitura p95 ≤ 2 ms, listagem p95 ≤ 20 ms, escrita p95 ≤ 10 ms; `EXPLAIN QUERY PLAN` coberto por teste.

**Testes:** admin sobrevive a banco removido/corrompido; triggers bloqueiam delete/update; criação/lote/login/desativação/exclusão; limpeza não toca no admin; cache invalida; migração preserva admin; benchmark e plano de consulta.

**Gate:** CA-40..CA-45 verdes; login do admin funciona com o arquivo SQLite removido; nenhum conteúdo de chat no banco.

---

### Fase 14 — Compatibilidade OpenAI (3 dias)

**Objetivo:** permitir que clientes OpenAI usem a NEXA API sem alteração.

**Tarefas**

- [x] `core/api_keys.py`: leitura de `COMPAT_API_KEYS`, comparação em tempo constante, prefixo/fingerprint e escopos.
- [x] Rate limit por API key (`COMPAT_API_KEY_RATE_PER_MIN`).
- [x] `POST /v1/chat/completions` JSON com `model`, `messages`, `temperature`, `top_p`, `max_tokens`, `stop`, `tools`, `tool_choice`, `response_format`, `user`.
- [x] Streaming SSE com `chat.completion.chunk` e `data: [DONE]`.
- [x] `GET /v1/models` no shape `{"object":"list","data":[...]}`.
- [x] Envelope de erro OpenAI e testes de contrato.

**Testes:** request/response golden; streaming; tools; modelo inválido; cota; API key ausente/inválida; erro de provedor; CORS.

**Gate:** CA-31..CA-33, CA-38, CA-39 verdes; um cliente OpenAI de teste consome a API sem adaptação.

---

### Fase 15 — Compatibilidade Anthropic / Claude Code (4 dias)

**Objetivo:** permitir que o Claude Code use a NEXA API como backend Anthropic.

**Tarefas**

- [x] `POST /v1/messages` JSON com `model`, `max_tokens`, `system` (string/blocos), `messages` (string/blocos), `tools`, `tool_choice`, `temperature`, `top_p`, `top_k`, `stop_sequences`, `metadata`.
- [x] Resposta com `content[]` (`text`, `tool_use`), `stop_reason`, `stop_sequence`, `usage`.
- [x] Streaming: `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, `ping`, `error`.
- [x] `POST /v1/messages/count_tokens` (contagem real ou estimativa documentada).
- [x] Headers `anthropic-version`, `x-api-key`/Bearer, `anthropic-beta` (aceitar/ignorar conforme documentado).
- [x] Envelope de erro Anthropic e testes de contrato.
- [x] Teste E2E com Claude Code apontando `ANTHROPIC_BASE_URL` para a API.

**Testes:** golden de request/response; streaming; tools/`tool_use`; system em blocos; `count_tokens`; modelo inválido; cota; API key; Claude Code E2E.

**Gate:** CA-34..CA-36 verdes; Claude Code executa uma tarefa com ferramenta contra o provedor fake.

---

### Fase 16 — Compatibilidade Ollama (2 dias)

**Objetivo:** permitir que clientes Ollama usem a NEXA API como se fosse um servidor Ollama.

**Tarefas**

- [x] `POST /api/chat` com `model`, `messages`, `stream`, `tools`, `options`, `format`, `keep_alive`; NDJSON.
- [x] `GET /api/tags` com metadados e `details`.
- [x] `POST /api/show` e `GET /api/ps` (agregado/opcional).
- [x] Auth configurável (`COMPAT_OLLAMA_ALLOW_LOCALHOST` somente desenvolvimento) e envelope de erro Ollama.
- [x] Testes de contrato NDJSON.

**Testes:** chat streaming/não-streaming; tags/show/ps; tools; modelo inválido; auth; erro de provedor.

**Gate:** CA-37, CA-38 verdes; um cliente Ollama de teste consome `/api/chat` e `/api/tags` (`tests/e2e/test_ollama_client.py`).

---

### Fase 17 — Hardening, performance e release (3 dias)

**Tarefas**

- [ ] Revisão de segurança: API keys, logs, admin protegido, SQL parametrizado, permissões do arquivo.
- [ ] Benchmarks finais e ajuste de índices/PRAGMAs; relatório no CI.
- [ ] OpenAPI nativo completo + OpenAPI separado de compatibilidade; snapshots.
- [ ] README, `.env.example`, SRS/plano/matriz/checklist consistentes.
- [ ] Rodada manual com Ollama real, OpenAI/Anthropic (fake ou sandbox) e Claude Code.
- [ ] Tag `v2.0.0`.

**Gate:** CA-01..CA-47 verdes; Ruff, mypy, cobertura ≥ 80%, benchmarks e build Docker sem falhas.

---

### Fases concluídas (histórico)

As fases **F0 a F11** da v1.x estão concluídas e permanecem registradas no histórico do documento. A v2.0 não as reimplementa; ela estende os contratos existentes.

| Fase | Entrega | Status |
|---|---|---|
| F0 | Fundação (config, erros, logging, health) | ✅ |
| F1 | Autenticação admin/guest | ✅ |
| F2 | Provedores Ollama/fake | ✅ |
| F3 | Cotas e limites | ✅ |
| F4 | Chat JSON | ✅ |
| F5 | Streaming SSE | ✅ |
| F6 | Administração | ✅ |
| F7 | Hardening | ✅ |
| F8 | Observabilidade | ✅ |
| F9 | Empacotamento | ✅ |
| F10 | Conformidade e release v1.0.0 | ✅ |
| F11 | Catálogo de modelos e release v1.1.0 | ✅ |

---

## 7. Estratégia de testes

### 7.1 Pirâmide

| Nível | O que cobre | Ferramentas |
|---|---|---|
| **Unitário** | security, users, api keys, clock, IP, rate limiter, usage store, stats, schemas, router, capabilities, adapters | pytest |
| **Integração (ASGI)** | rotas nativas e admin com fake provider e SQLite temporário | `httpx.ASGITransport` |
| **Contrato** | OpenAI, Anthropic e Ollama (request/response/stream) | pytest + golden files |
| **Performance** | SQLite (leitura, escrita, listagem) e overhead de compatibilidade | pytest-benchmark ou script próprio |
| **E2E** | SSE real com `uvicorn`, Claude Code e Ollama fake | pytest + subprocess |

### 7.2 Fixtures centrais (`conftest.py`)

- `settings_factory` — sobrescreve env por teste (inclui `ENVIRONMENT=development`, `DATABASE_URL=sqlite:///:memory:` ou arquivo temporário).
- `clock` — relógio controlável (`FreezeClock`).
- `app` / `client` — app limpo por teste, com banco migrado e admin semeado.
- `db` — conexão SQLite temporária com WAL e PRAGMAs de teste.
- `fake_provider` — modos: `ok`, `fail_before_first_chunk`, `fail_mid_stream`, `slow`, `length_limit`, `tool_use`.
- `provider_router` — registry com fake + provedores mockados.
- `guest_token` / `admin_token` / `test_user_token` / `compat_api_key`.
- `sse_parser` — converte `text/event-stream` em lista de eventos.
- `ndjson_parser` — converte NDJSON em lista de objetos.

### 7.3 Casos críticos (além dos CA)

- **Roteamento:** `provider/model` correto; alias; nome sem prefixo; allowlist com glob; modelo inexistente.
- **Capacidades:** `tools` em modelo sem suporte; `vision` sem imagem; `json_mode` em Anthropic.
- **Fallback:** falha antes do 1º token → próximo da cadeia; falha depois → sem fallback; cota única.
- **SQLite:** 50 leituras concorrentes + 10 escritas; WAL; `busy_timeout`; admin imutável; migração a partir de banco vazio e de banco v1.1.0.
- **Cache:** usuário desativado deixa de autenticar imediatamente; senha redefinida invalida o cache.
- **Compatibilidade:** golden de OpenAI/Anthropic/Ollama; streaming completo; erros; tools; `[DONE]`/`message_stop`/`done:true`.
- **Segurança:** API key ausente/inválida; chave de provedor nunca em log; SQL injection; tentativa de deletar admin.
- **Performance:** p95 de leitura/escrita/listagem; `EXPLAIN QUERY PLAN` usando índice.
- **Logs:** nenhum conteúdo, token, senha ou chave em qualquer nível.

### 7.4 Matriz CA → teste (resumo)

| CA | Fase | Tipo |
|---|---|---|
| 01..03 | 1 (v1) | integração |
| 04..06 | 4/5 (v1) | integração/e2e |
| 07..13 | 3/4/5 (v1) | integração |
| 14..23 | 2/6/7 (v1) | integração/unitário |
| 24..25 | 11 (v1) | integração |
| 26..30 | 12 | unitário/integração |
| 31..33 | 14 | contrato |
| 34..36 | 15 | contrato/e2e |
| 37..39 | 16/14 | contrato/segurança |
| 40..45 | 13 | integração/performance |
| 46..47 | 17 | documentação/CI |

---

## 8. Qualidade e CI

Pipeline (`.github/workflows/ci.yml`):

1. `ruff check .` + `ruff format --check .`
2. `mypy app`
3. `pytest --cov=app --cov-fail-under=80`
4. `pytest tests/performance -m performance` (job separado, tolerância de ambiente)
5. `docker build` (somente em `main`)
6. Publicação do relatório de cobertura e do benchmark como artefatos.

Regras:

- Falha em qualquer etapa bloqueia o merge.
- Cobertura medida em `app/api`, `app/core`, `app/ai`, `app/compat` e `app/db`.
- Proibido `# type: ignore` sem justificativa na mesma linha.
- `pytest -W error` para evitar warnings silenciosos.
- Benchmarks usam ambiente controlado; falhas de performance geram alerta, não bloqueio, exceto regressão > 20%.

---

## 9. Configuração e ambientes

- `.env.example` com **todas** as variáveis do SRS §3.1.4, agrupadas por área e com comentários de faixa.
- `Settings` valida: inteiros positivos, URLs, `LOG_LEVEL`, `ENVIRONMENT`, `CORS_ORIGINS`, `TRUSTED_PROXIES`, `JWT_SECRET`, `DATABASE_URL` (somente `sqlite:///`), provedores habilitados e credenciais.
- Perfis:
  - `development`: `/docs` aberto, CORS `http://localhost:5173`, logs `DEBUG`, `COMPAT_ALLOW_ANONYMOUS` permitido apenas localmente.
  - `production`: HTTPS no proxy, segredo forte, admin obrigatório, API keys obrigatórias, WAL e backup habilitados.
- Segredos nunca no repositório; `.env` já ignorado.
- O volume `./data` deve ser persistente no Docker e ter permissão restrita.

---

## 10. Riscos e mitigação

| Risco | Impacto | Mitigação | Fase |
|---|---|---|---|
| Divergência entre APIs de provedores | Falhas de contrato e retrabalho | Adapters isolados + testes de contrato com `respx` e golden files | 12 |
| Claude Code exigir detalhes não previstos | Compatibilidade incompleta | Teste E2E com Claude Code desde o início da F15; documentar headers/campos ignorados | 15 |
| SQLite com contenção de escrita | Latência e `SQLITE_BUSY` | WAL, uma conexão de escrita, transações curtas, `busy_timeout`, pool de leitura | 13 |
| Perda/corrupção do SQLite | Usuários de teste indisponíveis | Backup antes de migrar, `quick_check`, fallback do admin, restauração documentada | 13 |
| Admin apagado por engano | Perda de acesso administrativo | Triggers + guarda na aplicação + fallback de configuração + teste dedicado | 13 |
| Vazamento de chaves | Incidente de segurança | Redaction, mascaramento, testes de log, nunca retornar chaves | 12/17 |
| Fallback duplicar custo/resposta | Custo e inconsistência | Fallback só antes do 1º token; cota única; teste de falha | 12 |
| Tradução de streaming quebrar clientes | Experiência ruim | Testes de contrato e parsers reais; `[DONE]`/`message_stop`/`done:true` | 14/15/16 |
| Performance do banco abaixo da meta | Requisito não atendido | Benchmark contínuo, índices, `EXPLAIN QUERY PLAN`, cache | 13/17 |
| Escopo crescer (histórico, billing) | Atraso | Escopo congelado no SRS v2.0; mudanças entram como nova fase | — |

---

## 11. Cronograma

### 11.1 Premissas

- 1 desenvolvedor sênior, 8 h/dia, com revisão de PR no mesmo dia.
- Início em **30/10/2026** (sexta-feira); fins de semana e feriados nacionais não contabilizados.
- Escopo congelado no SRS v2.0; mudanças entram como nova fase.

### 11.2 Cronograma de 1 desenvolvedor (21 dias úteis)

| Fase | Dias | Início | Fim | Marco |
|---|---|---|---|---|
| F12 — Gateway multi-provedor | 5 | 30/10 | 05/11 | **M8**: roteamento e catálogo multi-provedor |
| F13 — SQLite de usuários | 4 | 06/11 | 11/11 | **M9**: admin inviolável + usuários de teste + performance |
| F14 — Compat OpenAI | 3 | 12/11 | 16/11 | **M10**: clientes OpenAI funcionando |
| F15 — Compat Anthropic | 4 | 17/11 | 20/11 | **M11**: Claude Code funcionando |
| F16 — Compat Ollama | 2 | 23/11 | 24/11 | **M12**: clientes Ollama funcionando |
| F17 — Hardening/release | 3 | 25/11 | 27/11 | **M13**: CA-01..CA-47 verdes, `v2.0.0` |

**Buffer:** 3 dias úteis → release de risco em **02/12/2026**.

### 11.3 Cenário com 2 desenvolvedores (~13 dias úteis)

| Período | Dev A | Dev B |
|---|---|---|
| Dias 1–5 | F12 (adapters OpenAI/Anthropic) | F12 (Ollama/Google/router) |
| Dias 6–9 | F13 (conexão/migrações/repositório) | F13 (auth/cache/endpoints/benchmark) |
| Dias 10–11 | F14 (OpenAI) | F16 (Ollama) |
| Dias 12–13 | F15 (Anthropic/Claude Code) | F17 (hardening/docs) |

**Total:** ~13 dias + 3 de buffer ≈ **3 semanas** (release por volta de 27/11/2026).

> A paralelização depende de `F12-01` (capacidades) e `F13-01` (conexão) prontos no início; se atrasarem, os dois desenvolvedores convergem para o caminho crítico.

---

## 12. Definition of Done e checklist de aceitação

### 12.1 DoD por tarefa

- [ ] Código revisado e mesclado.
- [ ] Testes automatizados cobrindo o comportamento novo.
- [ ] `ruff`, `mypy` e `pytest` verdes.
- [ ] OpenAPI atualizado quando houver mudança de contrato.
- [ ] Migração versionada e testada (quando aplicável).
- [ ] Sem TODO/FIXME pendente sem issue.
- [ ] Logs sem dados sensíveis.

### 12.2 DoD por release

- [ ] CA-01 a CA-47 verificados e rastreados.
- [ ] Cobertura ≥ 80%.
- [ ] Benchmarks do SQLite dentro das metas.
- [ ] Imagem Docker publicada e healthcheck ok.
- [ ] `README` e `.env.example` completos.
- [ ] Rodada manual com Ollama real, provedores de nuvem (fake/sandbox) e Claude Code.
- [ ] Tag `v2.0.0`.

### 12.3 Checklist de aceitação (SRS §4)

- [ ] CA-01 boot exige credenciais válidas
- [ ] CA-02 login ok / 401 genérico
- [ ] CA-03 guest usa o chat sem cadastro
- [ ] CA-04 SSE incremental com `done`
- [ ] CA-05 JSON completo
- [ ] CA-06 nada persistido, nada em log
- [ ] CA-07 cota diária → 429 + `resets_at`
- [ ] CA-08 limite por minuto → 429 + `Retry-After`
- [ ] CA-09 novo token não renova cota por IP
- [ ] CA-10 emissão de token limitada por IP
- [ ] CA-11 `/usage` coerente com headers
- [ ] CA-12 admin sem cota; guest/usuário 403 no admin
- [ ] CA-13 refund de cota em falha inicial
- [ ] CA-14 provedor parado → 502/504/evento e resto funciona
- [ ] CA-15 troca de provedor por config (fake)
- [ ] CA-16 lockout de login
- [ ] CA-17 OpenAPI nativo completo com exemplos
- [ ] CA-18 cobertura ≥ 80% e pipeline verde
- [ ] CA-19 headers legíveis no browser (CORS)
- [ ] CA-20 listagem + reset por `gid`
- [ ] CA-21 limites de saída com `finish_reason`
- [ ] CA-22 401 com `WWW-Authenticate`; 422 com `errors[]`
- [ ] CA-23 recusa múltiplos workers sem Redis
- [ ] CA-24 catálogo `/models` com metadados e `selectable`
- [ ] CA-25 seleção de modelo por requisição, validação sem cota
- [ ] CA-26 roteamento `provider/model`
- [ ] CA-27 boot valida credenciais dos provedores
- [ ] CA-28 catálogo agregado com cache
- [ ] CA-29 capacidade incompatível → 422 sem cota
- [ ] CA-30 fallback antes do 1º token com cota única
- [ ] CA-31 OpenAI JSON
- [ ] CA-32 OpenAI streaming + `[DONE]`
- [ ] CA-33 OpenAI `/v1/models`
- [x] CA-34 Anthropic JSON
- [x] CA-35 Anthropic streaming + tools
- [x] CA-36 Claude Code E2E
- [x] CA-37 Ollama NDJSON + tags
- [x] CA-38 auth/cotas nas rotas de compatibilidade
- [x] CA-39 chaves nunca expostas
- [x] CA-40 login do admin sem SQLite
- [x] CA-41 admin inviolável (API + triggers)
- [x] CA-42 ciclo de vida de usuários de teste
- [x] CA-43 metas de performance do SQLite
- [x] CA-44 migrações preservam admin + backup
- [x] CA-45 somente usuários no banco
- [ ] CA-46 documentação consistente
- [ ] CA-47 contratos de compatibilidade verdes

---

## 13. Anexos

### A. Matriz requisito → módulo → teste

| Requisito | Módulo | Teste |
|---|---|---|
| RF-AUT-01..13 | `core/auth.py`, `core/security.py`, `core/api_keys.py`, `api/auth.py`, `api/deps.py` | `tests/integration/test_auth.py`, `tests/unit/test_security.py`, `tests/unit/test_api_keys.py` |
| RF-CHAT-01..17 | `api/chat.py`, `ai/router.py`, `ai/providers/*`, `core/sse.py`, `core/chat_stream.py`, `schemas/chat.py` | `tests/integration/test_chat.py`, `tests/unit/test_chat_stream.py`, `tests/unit/test_ai_router.py` |
| RF-USO-01..16 | `core/usage_store.py`, `core/rate_limit.py`, `core/usage.py` | `tests/unit/test_usage_store.py`, `tests/unit/test_rate_limit.py`, `tests/integration/test_usage_api.py` |
| RF-ADM-01..10 | `api/admin.py`, `api/users.py`, `core/stats.py` | `tests/integration/test_admin.py`, `tests/integration/test_users.py`, `tests/unit/test_stats.py` |
| RF-SIS-01..06 | `api/system.py`, `core/middleware.py`, `core/errors.py`, `db/connection.py` | `tests/integration/test_system.py`, `tests/unit/test_errors.py`, `tests/unit/test_middleware.py` |
| RF-GTW-01..10 | `ai/registry.py`, `ai/router.py`, `ai/capabilities.py`, `ai/providers/*` | `tests/unit/test_ai_router.py`, `tests/unit/test_providers_*.py`, `tests/integration/test_models.py` |
| RF-CMP-01..12 | `compat/*`, `schemas/*` | `tests/contract/test_openai.py`, `test_anthropic.py`, `test_ollama.py`, `tests/e2e/test_claude_code.py` |
| RF-USR-01..15 | `db/*`, `core/users.py`, `api/users.py` | `tests/integration/test_users.py`, `tests/unit/test_user_repository.py`, `tests/performance/test_sqlite.py` |
| RNF-SEG-* | `core/middleware.py`, `core/security.py`, `core/api_keys.py`, `core/ip.py`, `config.py` | `tests/integration/test_system.py`, `tests/integration/test_auth.py`, `tests/unit/test_middleware.py` |
| RNF-OBS-* | `core/logging.py`, `core/middleware.py` | `tests/unit/test_logging.py`, `tests/integration/test_observability.py` |
| RNF-CON-* | `ai/*`, `core/chat_stream.py`, `db/connection.py`, `main.py` | `tests/unit/test_chat_stream.py`, `tests/integration/test_system.py`, `tests/unit/test_entrypoint.py` |
| RNF-DES-06..12 | `db/*`, `core/users.py` | `tests/performance/test_sqlite.py`, `scripts/db_benchmark.py` |
| RNF-MAN-* | estrutura + CI | `.github/workflows/ci.yml`, `pyproject.toml`, `requirements*.txt` |
| RNF-POR-* | `Dockerfile`, `docker-compose.yml`, `app/__main__.py` | `tests/unit/test_entrypoint.py`, `docker compose config`, build no CI |
| Contrato OpenAPI | `main.py`, `api/*`, `compat/*`, `schemas/*` | `tests/integration/test_openapi_contract.py`, `tests/snapshots/` |

### B. Comandos previstos (`Makefile`)

```bash
make setup          # uv sync + pre-commit install
make run            # uvicorn app.main:create_app --factory --reload
make migrate        # aplica migrações do SQLite
make test           # pytest
make cov            # pytest --cov=app --cov-report=term-missing
make bench          # benchmark do SQLite
make lint           # ruff check . && ruff format --check .
make type           # mypy app
make docker-build   # docker build -t nexa-api .
make docker-up      # docker compose up --build
```

### C. Convenções de branch e commit

- Branches: `feat/f12-gateway`, `feat/f13-sqlite-users`, `feat/f14-compat-openai`, `feat/f15-compat-anthropic`, `feat/f16-compat-ollama`, `fix/...`.
- Commits: `feat(ai): adiciona adapter Anthropic`, `test(db): cobre admin imutável`.
- Um PR por tarefa da fase; merge com squash.

### D. Pendências herdadas do SRS (§5.3)

1. Conjunto inicial de modelos/provedores em produção.
2. Valores de cota dos usuários de teste.
3. Fallback habilitado por padrão?
4. Múltiplos nós antes do Redis.
5. API key obrigatória na compatibilidade Ollama.
6. Retenção de usuários de teste.
7. `count_tokens` real vs. estimado.
8. Prazo máximo do graceful shutdown.

> Itens 1, 2 e 5 podem bloquear a **aceitação final** (fase 17), mas não o desenvolvimento, pois são configuráveis.
