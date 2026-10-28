# Plano de Implementação — API do Chat de IA

## Backend Python/FastAPI conforme SRS v1.1

| Campo | Valor |
|---|---|
| **Projeto** | Chat de IA via Web: Backend (API) |
| **Documento** | Plano de Implementação |
| **Versão** | 1.1 |
| **Data** | 28/10/2026 |
| **Documento de referência** | `docs/srs-api-chat-ia-v1.md` (SRS v1.1) |
| **Escopo** | Implementação da API REST especificada no SRS v1.1 (sem banco de dados) |
| **Público-alvo** | Desenvolvedores, QA e responsável técnico do projeto |

### Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 1.1 | 28/10/2026 | Reordenação das fases (cotas antes do chat, para eliminar *stubs*); backlog atômico com dependências e caminho crítico; versões mínimas e alternativas de stack; cronograma datado com marcos e buffer; ajuste dos gates de CA e da matriz de testes |
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

Uma **API REST em Python/FastAPI**, stateless e sem banco de dados, que:

- autentica o **administrador** por credenciais vindas de variáveis de ambiente;
- emite **tokens de convidado (guest)** anônimos com limites de uso;
- expõe chat com **resposta completa (JSON)** e **streaming (SSE)**;
- aplica **cotas por `gid` e por IP**, limites por minuto e limites de contexto;
- oferece ao administrador **estatísticas em memória**, **status do provedor**, **listagem de guests** e **reset de cota**;
- abstrai o provedor de IA (**Ollama** agora; contrato de nuvem preparado) por configuração.

### 1.2 Fora de escopo (herdado do SRS §5.1)

- Frontend React, banco de dados, cadastro público, histórico, feedback, e-mail, refresh token, auditoria persistente, billing, MFA, upload/RAG.
- Implementação real do `CloudProvider` (apenas contrato + esqueleto).
- Redis de verdade na v1 (interface pronta; implementação opcional fica para depois, salvo necessidade de múltiplos processos).

### 1.3 Resultado esperado

API executável localmente e em contêiner, com:

- 100% dos endpoints do SRS (§3.7) implementados e documentados no OpenAPI;
- todos os **CA-01 a CA-23** cobertos por teste automatizado;
- cobertura ≥ **80%** em `app/api`, `app/core` e `app/ai`;
- pipeline de lint, tipagem e testes verde.

---

## 2. Estratégia de execução

### 2.1 Abordagem

**Fatias verticais incrementais**, cada uma entregando um caminho utilizável de ponta a ponta, com testes no mesmo PR:

1. **Fundação** — projeto, config, erros, logging, health.
2. **Autenticação** — admin e guest.
3. **IA** — interface + Ollama + fake.
4. **Cotas** — store, janelas, `/usage` e helpers de cabeçalho.
5. **Chat JSON** — validação, integração com cotas e resposta completa.
6. **Streaming SSE** — refund, heartbeat, cancelamento.
7. **Administração** — stats, status, guests, reset.
8. **Hardening** — segurança, CORS, limites, brute force.
9. **Empacotamento** — Docker, compose, shutdown gracioso.
10. **Conformidade** — matriz de CA, cobertura, OpenAPI final.

> **Por que cotas antes do chat:** o chat (JSON e SSE) precisa consumir cota, emitir `X-RateLimit-*` e devolver cota em falha. Com o `UsageStore` já testado, a primeira integração do chat nasce correta em vez de receber um *stub* descartável.

Cada fase termina em um **gate** (seção 6) que precisa estar verde antes da próxima.

### 2.2 Princípios

- **Sem banco**: nenhum ORM, migração ou driver SQL no `pyproject`.
- **Um processo**: a API recusa `UVICORN_WORKERS > 1` sem `REDIS_URL` (RNF-CON-06).
- **Fronteiras explícitas**: rotas não conhecem `httpx`/Ollama; só `AIProvider`. Rotas não conhecem dicionários de contador; só `UsageStore`.
- **Erros como contrato**: toda exceção vira *Problem Details* com `code` estável.
- **Conteúdo nunca em log**: o pipeline de chat só emite metadados.
- **Tempo injetável**: relógio (`Clock`) e janelas testáveis sem `sleep`.
- **Fail-fast**: configuração inválida impede o boot.

### 2.3 Fluxo de trabalho

- Branch `main` protegida; PRs pequenos por tarefa.
- *Conventional Commits*: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.
- Nenhum PR sem teste correspondente.
- Revisão obrigatória em: `core/security.py`, `core/usage_store.py`, `api/chat.py`.

---

## 3. Stack e decisões técnicas

| Área | Escolha | Motivo |
|---|---|---|
| Runtime | **Python 3.12** (mínimo 3.11) | SRS §2.4; suporte a `asyncio.TaskGroup` e tipagem moderna |
| Framework | **FastAPI** + **Uvicorn** (1 worker) | RP-01, RP-05 |
| Validação | **Pydantic v2** + `pydantic-settings` | RNF-SEG-06, RNF-POR-02 |
| JWT | **PyJWT** | RP-03 |
| Hash | **argon2-cffi** (`argon2id`) | RNF-SEG-01 |
| HTTP cliente | **httpx** (async) | RP-04, RNF-DES-03 |
| Testes | **pytest**, `pytest-asyncio`, `httpx.ASGITransport`, `respx` | Sem porta real; mock de Ollama |
| Cobertura | **coverage.py** com gate de 80% | RNF-MAN-06, CA-18 |
| Lint/format | **Ruff** (`check` + `format`) | RNF-MAN-07 |
| Tipagem | **mypy** (strict nos módulos de domínio) | RNF-MAN-07 |
| Dependências | **uv** + `requirements.txt` compilado | RNF-MAN-08 |
| Logs | **structlog** (JSON) | RNF-OBS-01 |
| Contêiner | **Docker** multi-stage + **docker-compose** (API + Ollama opcional) | RNF-POR-01 |
| CI | **GitHub Actions** | RNF-MAN-07 |

Dependências principais (pinadas no `requirements.txt`):

```
fastapi
uvicorn[standard]
pydantic
pydantic-settings
PyJWT
argon2-cffi
httpx
structlog
```

Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `ruff`, `mypy`, `types-*` necessários.

### 3.2 Versões mínimas e alternativas avaliadas

| Componente | Mínimo | Alternativa descartada | Motivo da escolha |
|---|---|---|---|
| Python | 3.12 (aceita 3.11) | — | SRS §2.4; `TaskGroup` e tipagem moderna |
| FastAPI | 0.115 | Flask | Async nativo, OpenAPI automático (RF-SIS-02) |
| Pydantic | 2.9 | dataclasses | Validação declarativa e `extra="forbid"` |
| Uvicorn | 0.30 | Hypercorn | Simplicidade e suporte a `--workers 1` |
| PyJWT | 2.9 | `python-jose` | Mantido, menor superfície e sem dependência de criptografia pesada |
| argon2-cffi | 23 | `passlib[argon2]` | `passlib` sem manutenção ativa; uso direto do `argon2id` |
| httpx | 0.27 | `aiohttp` | Cliente único para runtime e testes ASGI |
| structlog | 24 | `logging` + formatador | Contexto por requisição e redaction simples; fallback viável se quisermos zero dependência extra |
| Rate limiter | próprio | `slowapi` / `fastapi-limiter` | Precisa de janela fixa **e** deslizante, refund e O(1); `fastapi-limiter` exige Redis |
| pytest | 8 | unittest | Fixtures e parametrização |
| pytest-asyncio | 0.24 | `anyio` puro | Integração direta com `httpx.ASGITransport` |
| respx | 0.21 | `responses` | Funciona com `httpx` async |
| Ruff | 0.7 | flake8 + black + isort | Uma ferramenta só (RNF-MAN-07) |
| mypy | 1.11 | pyright | Já previsto no SRS; CI simples |

**Versionamento:** esta tabela fixa o **mínimo aceitável**; as versões exatas ficam no `requirements.txt` compilado (RNF-MAN-08) e no `uv.lock`. Atualizações passam por PR com CI verde.

**Nota sobre `uv`:** usado para resolver/compilar dependências; o runtime continua instalando via `requirements.txt`, o que mantém compatibilidade com o Dockerfile e com o SRS.

---

## 4. Arquitetura

### 4.1 Visão de camadas

```
HTTP (FastAPI)
  │  middlewares: request_id, body limit, security headers, CORS, logging
  ▼
api/ (rotas)  ── schemas/ (Pydantic) ── core/errors.py (Problem Details)
  │
  ├── core/auth.py        (AuthService: credenciais da config)
  ├── core/security.py    (hash, JWT)
  ├── core/rate_limit.py  (login, emissão de token, por minuto)
  ├── core/usage_store.py (UsageStore: gid + IP)
  ├── core/stats.py       (métricas em memória)
  └── ai/factory.py ── ai/base.py (AIProvider) ── ollama.py | cloud.py | fake.py
```

### 4.2 Estrutura de diretórios

```
NEXA_API/
├── app/
│   ├── __init__.py
│   ├── main.py                  # create_app(), lifespan, routers, middleware
│   ├── config.py                # Settings + validação fail-fast
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py              # dependências: settings, usuário, store, provider
│   │   ├── auth.py              # POST /auth/login, POST /auth/guest
│   │   ├── chat.py              # POST /chat (JSON + SSE)
│   │   ├── usage.py             # GET /usage
│   │   ├── admin.py             # GET /admin/stats, /admin/ai/status, /admin/guests;
│   │   │                        # POST /admin/usage/reset
│   │   └── system.py            # GET /health, GET /health/ready
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py          # hash/verify, JWT encode/decode
│   │   ├── auth.py              # AuthService
│   │   ├── rate_limit.py        # janela fixa (dia) + deslizante (minuto)
│   │   ├── usage_store.py       # UsageStore (Protocol) + InMemoryUsageStore
│   │   ├── stats.py             # contadores monotônicos + HLL/lista limitada
│   │   ├── errors.py            # exceções + handlers + códigos
│   │   ├── logging.py           # JSON, mascaramento de segredos
│   │   ├── middleware.py        # request_id, body limit, headers
│   │   ├── ip.py                # IP real com TRUSTED_PROXIES
│   │   └── clock.py             # Clock injetável
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── base.py              # AIProvider + result types
│   │   ├── factory.py           # build_provider(settings)
│   │   ├── ollama.py            # POST {OLLAMA_URL}/api/chat
│   │   ├── cloud.py             # esqueleto (erro de config se selecionado)
│   │   └── fake.py              # provedor determinístico p/ testes
│   └── schemas/
│       ├── __init__.py
│       ├── common.py            # ErrorEnvelope, ErrorItem, UsageInfo
│       ├── auth.py              # LoginRequest, TokenResponse, GuestTokenResponse
│       ├── chat.py              # Message, ChatRequest, ChatResponse
│       ├── admin.py             # StatsResponse, AIStatusResponse, GuestListResponse,
│       │                        # UsageResetRequest
│       └── system.py            # HealthResponse, ReadyResponse
├── tests/
│   ├── conftest.py              # app, client, clock, fake provider, settings
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
│   ├── srs-api-chat-ia-v1.md
│   └── plano-implementacao-api-chat-ia-v1.md
├── scripts/
│   └── hash_password.py         # gera ADMIN_PASSWORD_HASH
├── .github/workflows/ci.yml
├── .env.example
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── .dockerignore
└── README.md
```

### 4.3 Responsabilidades por módulo

| Módulo | Responsabilidade | Não faz |
|---|---|---|
| `api/*` | parsing, autorização, orquestração, resposta | falar com Ollama diretamente; guardar contadores |
| `schemas/*` | contrato de entrada/saída (`extra="forbid"`) | regra de negócio |
| `core/security.py` | hash/verify, emitir/validar JWT | decidir permissões |
| `core/auth.py` | verificar credenciais e montar claims | conhecer banco |
| `core/rate_limit.py` | janelas de login/token/minuto | cotas diárias |
| `core/usage_store.py` | cota diária `gid`+IP, refund, reset, listagem | autenticação |
| `core/stats.py` | métricas em memória | persistência |
| `core/errors.py` | catálogo de códigos + Problem Details | regra de negócio |
| `ai/*` | tradução do protocolo do provedor | autenticação, cota, SSE |
| `core/middleware.py` | request_id, limite de corpo, headers | regra de negócio |

### 4.4 Interfaces principais (assinaturas de referência)

```python
# ai/base.py
class AIProvider(Protocol):
    name: str
    model: str
    async def generate(self, messages: list[Message], *, max_tokens: int, timeout: float) -> GenerationResult: ...
    def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]: ...
    async def health(self) -> bool: ...

@dataclass
class GenerationResult:
    content: str
    finish_reason: Literal["stop", "length", "timeout"]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
```

```python
# core/usage_store.py
class UsageStore(Protocol):
    async def consume(self, *, gid: str, ip: str, now: datetime) -> UsageInfo: ...   # levanta QuotaExceeded
    async def refund(self, *, gid: str, ip: str, now: datetime) -> None: ...
    async def snapshot(self, *, gid: str, ip: str, now: datetime) -> UsageInfo: ...
    async def reset(self, *, scope: Literal["gid", "ip"], value: str) -> bool: ...
    async def list_guests(self, *, now: datetime) -> GuestListResponse: ...
```

```python
# core/clock.py
class Clock(Protocol):
    def now(self) -> datetime: ...   # sempre UTC
```

`UsageInfo` é o schema único exigido por RD-08, com `daily_limit`, `daily_remaining`, `per_minute_limit`, `per_minute_remaining` e `resets_at`.

### 4.5 Pipeline do chat

```
1. autenticar (JWT)            → 401 com WWW-Authenticate
2. validar schema (Pydantic)   → 422 com errors[]
3. aplicar limites de contexto → 422
4. usage.consume(gid, ip)      → 429 (guest_daily_limit_reached | ip_daily_limit_reached | rate_limited)
5. montar contexto             → DEFAULT_SYSTEM_PROMPT + mensagens validadas
6. provider.stream/generate    → 502/504 (antes do 1º trecho) | evento error (depois)
7. responder                   → JSON ou SSE (+ done) com usage e finish_reason
8. métricas/logs               → metadados apenas
9. falha antes do 1º trecho    → usage.refund(gid) + usage.refund(ip)
```

### 4.6 Middlewares e ordem

1. `RequestIDMiddleware` — aceita UUID válido ou gera; injeta no estado e no log.
2. `BodyLimitMiddleware` — lê `Content-Length` e conta bytes no stream; `413`.
3. `SecurityHeadersMiddleware` — `nosniff`, `Cache-Control` para não-SSE.
4. `CORS` — origens, métodos, `expose_headers`.
5. `LoggingMiddleware` — duração, status, `request_id`, sem corpo.
6. Handlers de exceção — `HTTPException`, `RequestValidationError`, `QuotaExceeded`, `ProviderError`, `TimeoutError`, `Exception`.

---

## 5. Contratos e modelos

| Modelo | Campos principais | Regras |
|---|---|---|
| `Settings` | todas as variáveis de §3.1.4 do SRS | validação de tipo/faixa; recusa boot inválido |
| `LoginRequest` | `username`, `password` | `extra="forbid"` |
| `TokenResponse` | `access_token`, `token_type`, `expires_in`, `role` | `token_type="bearer"` |
| `GuestTokenResponse` | `TokenResponse` + `usage` | `role="guest"` |
| `Message` | `role` ∈ {system,user,assistant}, `content` | `content` não vazio após `strip` |
| `ChatRequest` | `messages` (1..N), `stream` (default `true`) | última = `user`; sem campos extras |
| `ChatResponse` | `message`, `provider`, `model`, `finish_reason`, `usage` | `finish_reason` ∈ {stop,length,timeout} |
| `UsageInfo` | 5 campos de RD-08 | admin: `daily_*` e `resets_at` nulos |
| `ErrorEnvelope` | `type`, `title`, `status`, `code`, `detail`, `request_id`, `errors?`, `resets_at?` | RFC 9457 estendida |
| `StatsResponse` | `since`, `chat_requests_total`, `guest_distinct`, `rate_limited_total`, `provider_errors_total`, `avg_response_seconds` | |
| `AIStatusResponse` | `provider`, `model`, `available`, `checked_at` | nunca expõe chave |
| `GuestListResponse` | `guests[]`, `ip_usage[]` | `gid`, `ip`, `daily_used`, `daily_limit`, `last_seen_at` |
| `UsageResetRequest` | `scope` ∈ {gid,ip}, `value` | `404` se alvo não existir |
| `HealthResponse` | `status`, `version`, `request_id` | |
| `ReadyResponse` | `status`, `provider?` | `503` se indisponível |

**Claims JWT:** `sub` (admin username ou `gid`), `role` (`admin`/`guest`), `iat`, `exp`. Sem `sid`/refresh (fora de escopo).

---

## 6. Fases de implementação

> Estimativas em dias de 1 desenvolvedor sênior, incluindo testes e revisão.

### Backlog atômico e dependências

Tamanhos: **S** ≈ 2–4 h, **M** ≈ 4–6 h, **L** ≈ 1 dia. Cada tarefa entra em um PR próprio (ou PR pequeno por par).

| ID | Tarefa | Tam. | Depende de | Artefato principal |
|---|---|---|---|---|
| F0-01 | Bootstrap (pyproject, deps, Makefile, pre-commit, CI) | M | — | `pyproject.toml`, `requirements*.txt`, CI |
| F0-02 | `Settings` + validação fail-fast | M | F0-01 | `app/config.py` |
| F0-03 | `Clock` + IP real (`TRUSTED_PROXIES`) | S | F0-02 | `core/clock.py`, `core/ip.py` |
| F0-04 | Catálogo de erros + handlers | M | F0-02 | `core/errors.py` |
| F0-05 | Logging JSON + redaction | M | F0-04 | `core/logging.py` |
| F0-06 | Middlewares (request_id, body limit, headers) | M | F0-05 | `core/middleware.py` |
| F0-07 | `create_app` + `/health` + `/health/ready` | S | F0-06 | `main.py`, `api/system.py` |
| F1-01 | `security.py` (argon2id + JWT) | M | F0-04 | `core/security.py` |
| F1-02 | `AuthService` em tempo constante | M | F1-01 | `core/auth.py` |
| F1-03 | `POST /auth/login` | S | F1-02 | `api/auth.py` |
| F1-04 | `POST /auth/guest` + `usage` inicial | M | F1-01 | `api/auth.py` |
| F1-05 | Rate limit de login/emissão + lockout | M | F0-03 | `core/rate_limit.py` |
| F1-06 | `get_current_user` / `require_admin` | S | F1-01 | `api/deps.py` |
| F2-01 | `AIProvider` + exceções de domínio | S | F0-04 | `ai/base.py` |
| F2-02 | `OllamaProvider.generate` | M | F2-01 | `ai/ollama.py` |
| F2-03 | `OllamaProvider.stream` | M | F2-02 | `ai/ollama.py` |
| F2-04 | Factory + esqueleto `CloudProvider` | S | F2-02 | `ai/factory.py`, `ai/cloud.py` |
| F2-05 | `fake` provider + fixtures `respx` | M | F2-01 | `ai/fake.py`, `tests/conftest.py` |
| F2-06 | `health()` + timeouts | S | F2-03 | `ai/*` |
| F3-01 | `UsageStore` + janela diária (`gid`/IP) | M | F0-03 | `core/usage_store.py` |
| F3-02 | Janela deslizante de 60 s | S | F3-01 | `core/usage_store.py` |
| F3-03 | `consume` atômico + `refund` | M | F3-01 | `core/usage_store.py` |
| F3-04 | `snapshot`, `reset`, `list_guests` | M | F3-01 | `core/usage_store.py` |
| F3-05 | `GET /usage` + `UsageInfo` | S | F3-04 | `api/usage.py` |
| F3-06 | Helpers `X-RateLimit-*` | S | F3-04 | `core/usage_store.py` |
| F3-07 | Guarda de workers + limpeza de chaves | S | F3-01 | `main.py` |
| F4-01 | `schemas/chat` + validação | M | F0-04 | `schemas/chat.py` |
| F4-02 | `/chat` `stream=false` + `consume` | M | F2-04, F3-03 | `api/chat.py` |
| F4-03 | Contexto, prompt padrão e política `system` | S | F4-02 | `api/chat.py` |
| F4-04 | Falhas 502/504 + refund | S | F4-02 | `api/chat.py`, `core/errors.py` |
| F4-05 | Limites de saída (`finish_reason`) | S | F4-02 | `api/chat.py` |
| F4-06 | Headers + métricas da resposta JSON | S | F3-06 | `api/chat.py` |
| F5-01 | `StreamingResponse` + headers | M | F4-02 | `api/chat.py` |
| F5-02 | Eventos `data`/`done`/`error` | M | F5-01 | `api/chat.py` |
| F5-03 | Heartbeat `: ping` | S | F5-02 | `api/chat.py` |
| F5-04 | Cancelamento/desconexão | M | F5-02 | `api/chat.py` |
| F5-05 | Refund no stream + `finish_reason` | M | F5-02, F3-03 | `api/chat.py` |
| F5-06 | Parser SSE de teste | S | F5-02 | `tests/conftest.py` |
| F6-01 | Stats em memória (HLL/expiração) | M | F0-03 | `core/stats.py` |
| F6-02 | `GET /admin/stats` | S | F6-01 | `api/admin.py` |
| F6-03 | `GET /admin/ai/status` | S | F2-06 | `api/admin.py` |
| F6-04 | `GET /admin/guests` | S | F3-04 | `api/admin.py` |
| F6-05 | `POST /admin/usage/reset` | S | F3-04 | `api/admin.py` |
| F6-06 | `require_admin` nas rotas + `403` | S | F1-06 | `api/admin.py` |
| F7-01 | CORS + `expose_headers` | S | F0-06 | `main.py` |
| F7-02 | `413` + `405/415` padronizados | S | F0-06 | `core/middleware.py` |
| F7-03 | Política de `system` e `extra="forbid"` global | S | F4-01 | `schemas/*` |
| F7-04 | `JWT_SECRET`/`ENVIRONMENT` | S | F0-02 | `config.py` |
| F7-05 | Teste de redaction de logs | S | F0-05 | `tests/unit/test_logging.py` |
| F7-06 | Spoof de XFF + normalização de IP | S | F0-03 | `core/ip.py` |
| F8-01 | Logs estruturados completos | M | F0-05 | `core/logging.py` |
| F8-02 | Eventos de segurança | S | F8-01 | `core/logging.py` |
| F8-03 | Correlação `request_id` ↔ provider | S | F8-01 | `ai/*` |
| F9-01 | `Dockerfile` + `HEALTHCHECK` | M | F0..F8 | `Dockerfile` |
| F9-02 | `docker-compose` + perfil Ollama | S | F9-01 | `docker-compose.yml` |
| F9-03 | Shutdown gracioso | M | F5-05 | `main.py` |
| F9-04 | `README` + alvos do `Makefile` | S | F9-01 | `README.md` |
| F10-01 | Matriz CA → teste | M | todas | `docs/` |
| F10-02 | Cobertura ≥ 80% | S | F10-01 | CI |
| F10-03 | OpenAPI + teste de snapshot | S | F10-01 | `tests/integration/test_openapi.py` |
| F10-04 | Rodada manual com Ollama real | S | F10-02 | relatório |
| F10-05 | Tag de release `v1.0.0` | S | F10-04 | git |

**Caminho crítico:** `F0-01 → F0-02 → F0-04 → F0-05 → F0-06 → F1-01 → F1-02 → F1-03 → F1-06 → F2-01 → F2-02 → F2-04 → F3-01 → F3-03 → F4-01 → F4-02 → F5-01 → F5-02 → F5-05 → F9-03 → F10-01`.

### Fase 0 — Fundação (2 dias)

**Tarefas**

- [x] Criar `pyproject.toml`, `requirements.txt`/`requirements-dev.txt`, `Makefile`, `.gitignore` (já existe), `.env.example`.
- [x] `config.Settings` com todas as variáveis e validação fail-fast.
- [x] `core/errors.py`: catálogo de `code`, `ProblemDetails`, handlers (`RequestValidationError` → `errors[]`).
- [x] `core/logging.py`: JSON + mascaramento (`Authorization`, `X-Api-Key`, `password`).
- [x] `core/middleware.py`: `request_id`, body limit, security headers.
- [x] `core/clock.py` + `core/ip.py` (`TRUSTED_PROXIES`).
- [x] `main.create_app()` + `/health` + `/health/ready` (sem provider real ainda).
- [x] `scripts/hash_password.py`.
- [x] CI inicial (lint, mypy, pytest) e pre-commit.

**Gate:** app sobe, `/health` responde com `X-Request-ID`, erros no formato do SRS, CI verde.

> **Concluída em 28/10/2026:** 96 testes, cobertura de 98% em `app/`, Ruff e mypy
> verdes; smoke test com `uvicorn` validado (`/health`, `/health/ready` e erro 404).

---

### Fase 1 — Autenticação (2 dias)

**Tarefas**

- [x] `core/security.py`: `hash_password`, `verify_password` (argon2id/bcrypt), `create_access_token`, `decode_access_token` (assinatura, `exp` via relógio injetável, `role`).
- [x] `core/auth.py`: `AuthService.authenticate(username, password)` com verificação em tempo constante e *dummy hash* para usuário inexistente (RF-AUT-04).
- [x] `POST /api/v1/auth/login` com `TokenResponse`.
- [x] `POST /api/v1/auth/guest`: `gid` UUIDv4, JWT `guest`, `usage` inicial; limite por IP (`GUEST_TOKENS_PER_IP_PER_HOUR`).
- [x] `core/rate_limit.py`: janela deslizante para login e emissão de token.
- [x] Bloqueio de login (`LOGIN_MAX_ATTEMPTS`/`LOGIN_LOCK_MINUTES`) → `429 login_locked` + `Retry-After`.
- [x] Dependências `get_current_user`/`require_admin` → `401` com `WWW-Authenticate` e `403` para guest.
- [x] `schemas/auth.py` e `schemas/usage.py` (`UsageInfo` único, RD-08).

**Testes:** login ok/401 genérico/timing equivalente/lockout; guest token + limite; JWT expirado/assinatura inválida/papel desconhecido; dependências e 403.

**Gate:** CA-01, CA-02, CA-03, CA-10, CA-16, CA-22.

> **Concluída em 28/10/2026:** 158 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; smoke test com `uvicorn` validado (login 200/401 com `WWW-Authenticate`,
> guest 200 com `gid` e `usage`).

---

### Fase 2 — Provedores de IA (2 dias)

**Tarefas**

- [x] `ai/base.py`: `AIProvider`, `ChatMessage`, `GenerationResult`/`GenerationChunk`, `FinishReason` e exceções `ProviderUnavailable`, `ProviderTimeout`, `ProviderError`, `ProviderConfigurationError`.
- [x] `ai/ollama.py`: `generate` e `stream` sobre `POST /api/chat`, mapeando `done`/`done_reason` e tokens quando disponíveis; `health()` em `GET /api/tags`.
- [x] `ai/cloud.py`: esqueleto do `CloudProvider` que falha com `ProviderConfigurationError` (§5.1).
- [x] `ai/factory.py`: registry `ollama` | `cloud` | `fake`, `build_provider`, `register_provider` e `available_providers`; cloud e nomes desconhecidos falham na inicialização com mensagem clara.
- [x] `ai/fake.py`: respostas determinísticas, modo falha (antes/depois de N trechos), modo lento, health controlável e registro de chamadas — usado em testes.
- [x] `health()` por provedor, ligado a `/health/ready` via `app.state.ai_health`; provider injetável em `create_app`.
- [x] Timeouts: total (`AI_TIMEOUT_SECONDS`) e inatividade no stream (`AI_STREAM_IDLE_TIMEOUT_SECONDS`), com mapeamento para `ProviderTimeout`.

**Testes:** `respx` simulando Ollama (sucesso, 500, timeout, conexão recusada, JSON inválido, stream completo/truncado/erro, health); factory e registro (CA-15); fake provider; `/health/ready` com fake saudável, doente e com exceção.

**Gate:** CA-14, CA-15 (com fake provider); `/health/ready` reflete indisponibilidade.

> **Concluída em 28/10/2026:** 224 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; smoke test confirma `/health` 200 e `/health/ready` 503 com o Ollama
> parado, e falha rápida e clara ao configurar `AI_PROVIDER=cloud`.

---

### Fase 3 — Cotas e limites (3 dias)

Objetivo: deixar o store de uso pronto e testado **antes** de integrá-lo ao chat.

**Tarefas**

- [x] `core/usage_store.py`: `UsageStore` (Protocol) + `InMemoryUsageStore` com:
  - janela diária fixa (00:00 UTC) por `gid` e por IP;
  - janela deslizante de 60 s por `gid` (máx. `GUEST_RATE_PER_MIN` marcas) e por admin;
  - `consume` atômico (`asyncio.Lock`), verificando as duas cotas antes de consumir;
  - `refund`, `snapshot`, `reset`, `list_guests`;
  - limpeza periódica de chaves expiradas via task de background (ligada ao `lifespan`).
- [x] `UsageInfo` único (RD-08); cota efetiva = `min(gid, ip)`; códigos `guest_daily_limit_reached` × `ip_daily_limit_reached` com prioridade do `gid` (RN-15).
- [x] Helpers de `X-RateLimit-*` (epoch seconds) reutilizáveis em JSON, SSE e 429; omitidos para admin.
- [x] `GET /usage` (guest e admin); a emissão de token guest passou a refletir o consumo real do IP.
- [x] Recusa de boot com `UVICORN_WORKERS > 1` sem Redis (RNF-CON-06) — validação de `Settings` desde a F0.
- [x] Admin sem cota diária, com limite por minuto.
- [x] `UsageStore` preparado para implementação Redis futura (sem implementá-la).

**Testes:** virada de dia e janela deslizante (clock fake), concorrência (`asyncio.gather`), refund duplo, precedência `gid` × IP, listagem/reset, headers e `/usage`.

**Gate:** CA-07, CA-08, CA-09, CA-11, CA-12 (parcial), CA-23.

> **Concluída em 28/10/2026:** 260 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; smoke test de `/usage` (guest 200, admin 200 com campos diários nulos,
> sem token 401) e da emissão de guest com `usage` vindo do store.

---

### Fase 4 — Chat JSON (2 dias)

**Tarefas**

- [x] `schemas/chat.py` (`extra="forbid"`, validação de `messages` e da última `user`).
- [x] `POST /chat` com `stream=false`: auth → validação → limites de contexto → `usage.consume` → provider → `ChatResponse`.
- [x] `DEFAULT_SYSTEM_PROMPT` prefixado; `ALLOW_CLIENT_SYSTEM_MESSAGES` (nunca para guest).
- [x] Limites de contexto por papel (422 com `errors[]`).
- [x] Falhas: `502 ai_provider_error`, `504 ai_provider_timeout`; refund se falhar antes do 1º token.
- [x] `finish_reason` e `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS` (teto de geração encerra com `finish_reason="timeout"`; timeout de transporte segue `504`).
- [x] Headers `X-RateLimit-*` na resposta JSON (inclusive nos erros de guest); métricas de duração/tokens em log estruturado (sem conteúdo).

**Testes:** sucesso, 422 (campos/limites), 422 `system` de guest, 429 com códigos, 502/504 + refund, limite de saída, headers.

**Gate:** CA-05, CA-12 (parcial), CA-13 (JSON), CA-21.

> **Concluída em 28/10/2026:** 322 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; smoke test do chat (400 `stream=true` provisório, 401 sem token, 422 de
> schema/limites, 502 do provedor, 429 de cota) com `X-RateLimit-*` em todas as
> respostas de guest e refund de cota nas falhas.

---

### Fase 5 — Streaming SSE (3 dias)

**Tarefas**

- [x] `StreamingResponse` com headers de 3.1.1 e `X-Accel-Buffering: no`.
- [x] Formato: `data: {"content": ...}`, `event: done`, `event: error`; heartbeat `: ping` a cada 15 s (`core/sse.py`).
- [x] Consumo de cota antes do stream; `done` com `finish_reason` e `usage`.
- [x] Primeiro trecho marca consumo definitivo; falha anterior dispara refund (`gid` + IP).
- [x] Cancelamento por desconexão (`CancelledError`/`aclose`) → fecha o stream do provider e aplica refund se antes do 1º trecho; o refund roda em task própria para sobreviver ao cancelamento do cliente.
- [x] Erros pós-início sem trocar o status HTTP.
- [x] Parser SSE nas fixtures de teste (`parse_sse` em `tests/conftest.py`).
- [x] Inatividade acima de `AI_STREAM_IDLE_TIMEOUT_SECONDS` encerra com evento `ai_provider_timeout` (RNF-CON-01).

**Testes:** fluxo completo, heartbeat, falha antes/depois do 1º trecho, desconexão no meio, limites de saída, headers.

**Gate:** CA-04, CA-13 (stream), CA-14 (evento `error`), CA-21.

> **Concluída em 28/10/2026:** 353 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; smoke test com servidor Ollama falso (chunks incrementais, `done` com
> `usage`, headers de SSE) e com o provedor parado (`event: error` + refund);
> desconexão real via `curl` validada antes do 1º trecho (refund) e no meio
> (cota mantida).

---

### Fase 6 — Administração (2 dias)

**Tarefas**

- [x] `core/stats.py`: totais, guests distintos (conjunto com expiração e teto), barrados, erros, média de duração.
- [x] `GET /admin/stats`, `GET /admin/ai/status`, `GET /admin/guests`, `POST /admin/usage/reset`.
- [x] `require_admin` em todas; guest → `403 forbidden`.
- [x] Reset `204` / `404`; lista com `gid` obtível para o reset (`list_guests` + `list_ip_usage` no `UsageStore`).
- [x] Documentação de exemplos no OpenAPI (`schemas/admin.py`, respostas `204`/`404`).

**Testes:** 403, schemas, reset por gid/ip, `404`, contadores coerentes após chat/429/erro.

**Gate:** CA-12, CA-17 (parcial), CA-20.

> **Concluída em 28/10/2026:** 384 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; contadores alimentados pelo chat JSON e pelo streaming (inclusive falhas
> e timeouts), guests distintos com expiração de 24 h e teto de memória.

---

### Fase 7 — Hardening de segurança (2 dias)

**Tarefas**

- [x] CORS completo (`expose_headers`, preflight) — RNF-SEG-04; `X-RateLimit-*` expostos também no `429`.
- [x] `413 payload_too_large` no middleware, antes de bufferizar (inclusive `Transfer-Encoding: chunked`).
- [x] `400/404/405/415` padronizados; `415` aplicado a corpos não-JSON (`ContentTypeMiddleware`).
- [x] Rejeição de `system` de guest e de campos desconhecidos em todos os corpos (`extra="forbid"`).
- [x] Validação de `JWT_SECRET` em produção (`ENVIRONMENT`); recusa de segredo fraco/ausente.
- [x] Máscaras de log e teste que injeta `Authorization` e verifica ausência de token/conteúdo.
- [x] `WWW-Authenticate` em 401; headers de segurança (`nosniff`, `DENY`, `no-referrer` e HSTS em produção).
- [x] Rate limit de login por IP com chave normalizada (IP real via `TRUSTED_PROXIES`).

**Testes:** CORS no browser (headers expostos), 413, 422, 401/403, spoof de `X-Forwarded-For` sem proxy confiável.

**Gate:** CA-06, CA-19, CA-22.

> **Concluída em 28/10/2026:** 405 testes, cobertura de 99% em `app/`, Ruff e mypy
> verdes; `415` para corpos não-JSON, HSTS em produção, lock de login pelo IP
> resolvido (com XFF só de proxy confiável) e ausência de token/conteúdo nos logs.

---

### Fase 8 — Observabilidade (1 dia)

**Tarefas**

- [ ] Logs JSON com `request_id`, rota, status, latência, papel, provedor, modelo, duração e tokens.
- [ ] Eventos de segurança: falha de login, lockout, cota atingida, erro de provedor.
- [ ] Correlação do `X-Request-ID` com o provider (header quando suportado).
- [ ] Teste de redaction.

**Gate:** RNF-OBS-01..05 verificados por teste de log.

---

### Fase 9 — Empacotamento e operação (2 dias)

**Tarefas**

- [ ] `Dockerfile` multi-stage (não-root), `HEALTHCHECK` em `/health`.
- [ ] `docker-compose.yml` com API (+ perfil opcional Ollama).
- [ ] `SIGTERM` com shutdown gracioso e prazo máximo; streams encerram com `done`.
- [ ] `README` com setup, variáveis, exemplos `curl` (JSON e SSE via `curl -N`).
- [ ] `Makefile`: `setup`, `run`, `test`, `lint`, `type`, `cov`, `docker-build`, `docker-up`.

**Gate:** imagem sobe, healthcheck ok, shutdown não derruba streams abruptamente.

---

### Fase 10 — Conformidade e entrega (2 dias)

**Tarefas**

- [ ] Preencher a matriz CA → teste (anexo A) e fechar lacunas.
- [ ] Cobertura ≥ 80% nas camadas exigidas; relatório no CI.
- [ ] Revisão do OpenAPI: todos os endpoints, exemplos, dois `content` no chat.
- [ ] Teste de contrato do OpenAPI (snapshot) para evitar regressão.
- [ ] Rodada manual de aceitação com Ollama real.
- [ ] Checklist de release e tag `v1.0.0`.

**Gate:** CA-01..CA-23 verdes; DoD da seção 12 completo.

---

## 7. Estratégia de testes

### 7.1 Pirâmide

| Nível | O que cobre | Ferramentas |
|---|---|---|
| **Unitário** | security, clock, IP, rate limiter, usage store, stats, schemas, factory, handlers | pytest |
| **Integração (ASGI)** | rotas completas sem rede, com `fake` provider e `InMemoryUsageStore` | `httpx.ASGITransport` |
| **E2E (processo)** | SSE real com `uvicorn` e Ollama fake (opcional) | pytest + subprocess |

### 7.2 Fixtures centrais (`conftest.py`)

- `settings_factory` — sobrescreve env por teste (inclui `ENVIRONMENT=development`).
- `clock` — relógio controlável (`FreezeClock`).
- `app` / `client` — app limpo por teste.
- `fake_provider` — modos: `ok`, `fail_before_first_chunk`, `fail_mid_stream`, `slow`, `length_limit`.
- `guest_token` / `admin_token`.
- `sse_parser` — converte `text/event-stream` em lista de eventos.

### 7.3 Casos críticos (além dos CA)

- Concorrência: 50 `consume` simultâneos com cota 10 → exatamente 10 sucessos.
- Virada de dia: `now = 23:59:59` → 1 request; `00:00:00` → cota cheia.
- Janela deslizante: 3 em 59 s ok; 4º em 60,1 s ok.
- Refund: falha antes do 1º trecho devolve `gid` **e** IP; falha depois não devolve.
- SSE: heartbeat não quebra o parser; `done` é o último evento; erro não vem após `done`.
- Logs: nenhum conteúdo de mensagem, token ou chave em qualquer nível.
- Config: cada variável inválida impede o boot (teste parametrizado).

### 7.4 Matriz CA → teste (resumo)

| CA | Fase | Tipo |
|---|---|---|
| 01, 02 | 1 | integração |
| 03, 10, 16 | 1 | integração |
| 04 | 5 | integração/e2e |
| 05 | 4 | integração |
| 06 | 7/8 | unitário (log) |
| 07, 08, 09, 11, 23 | 3 | integração |
| 12 | 4/5 | integração |
| 13 | 4/5 | integração |
| 14 | 2/5 | integração |
| 15 | 2 | integração |
| 17 | 6/10 | contrato |
| 18 | 10 | CI |
| 19 | 7 | integração (CORS) |
| 20 | 6 | integração |
| 21 | 4/5 | integração |
| 22 | 7 | integração |

---

## 8. Qualidade e CI

Pipeline (`.github/workflows/ci.yml`):

1. `ruff check .` + `ruff format --check .`
2. `mypy app`
3. `pytest --cov=app --cov-fail-under=80`
4. `docker build` (somente em `main`)
5. Publicação do relatório de cobertura como artefato.

Regras:

- Falha em qualquer etapa bloqueia o merge.
- Cobertura medida em `app/api`, `app/core`, `app/ai`; `main.py` e `config.py` contam, mas não compensam lacunas de regra.
- Proibido `# type: ignore` sem justificativa na mesma linha.
- `pytest -W error` para evitar warnings silenciosos.

---

## 9. Configuração e ambientes

- `.env.example` com **todas** as variáveis de §3.1.4 e comentários de faixa.
- `Settings` valida: inteiros positivos, URLs, `LOG_LEVEL`, `ENVIRONMENT`, `CORS_ORIGINS` (lista), `TRUSTED_PROXIES` (CIDRs), `JWT_SECRET` mínimo.
- Perfis:
  - `development`: `/docs` aberto, CORS `http://localhost:5173`, logs `DEBUG`.
  - `production`: exige HTTPS no proxy, segredo forte, `/docs` conforme decisão operacional.
- Segredos nunca no repositório; `.env` já ignorado.

---

## 10. Riscos e mitigação

| Risco | Impacto | Mitigação | Fase |
|---|---|---|---|
| Latência/throughput do Ollama local abaixo do esperado | RNF-DES-02/04 não atingidos | Medir cedo com o modelo real; ajustar `AI_MAX_OUTPUT_*` e concorrência | 2 |
| Contadores em memória perdidos no restart | Cota zerada (RN-12) | Aceito; documentar e preparar `UsageStore` para Redis/banco | 3 |
| SSE bloqueado por proxy | Cliente sem resposta incremental | Headers anti-buffer + heartbeat + teste atrás de proxy | 5 |
| `CloudProvider` ausente | Troca de provedor falha | Contrato + erro claro na inicialização + fake na factory | 2 |
| NAT compartilhado penaliza usuários | Bloqueio indevido | Cota por IP configurável; monitorar `ip_usage` no admin | 3 |
| Vazamento de conteúdo em logs | LGPD/segurança | Redaction + teste dedicado + revisão de PR | 7/8 |
| Complexidade do streaming (cancelamento/refund) | Cota inconsistente | Máquina de estados explícita + testes de falha | 5 |
| Dependência de hardware para aceitação | Atraso na entrega | Aceitação com fake provider + rodada manual separada | 10 |
| Estimativas otimistas / escopo crescendo | Atraso no release | Buffer de 15%, gates por fase e backlog atômico (F0-01…F10-05) | 0 |

---

## 11. Cronograma

### 11.1 Premissas

- 1 desenvolvedor sênior, 8 h/dia, com revisão de PR no mesmo dia.
- Início em **29/10/2026** (quinta-feira); fins de semana e feriados nacionais não contabilizados.
- Escopo congelado no SRS v1.1; mudanças entram como nova fase, não como “ajuste”.

### 11.2 Cronograma de 1 desenvolvedor (23 dias úteis)

| Fase | Dias | Início | Fim | Marco |
|---|---|---|---|---|
| F0 — Fundação | 2 | 29/10 | 30/10 | App sobe com `/health`, erros e logs |
| F1 — Autenticação | 2 | 02/11 | 03/11 | **M1**: admin e guest autenticando |
| F2 — Provedores de IA | 2 | 04/11 | 05/11 | **M2**: Ollama + fake plugáveis |
| F3 — Cotas | 3 | 06/11 | 10/11 | Store de uso testado e `/usage` |
| F4 — Chat JSON | 2 | 11/11 | 12/11 | **M3**: chat completo com cota |
| F5 — Streaming SSE | 3 | 13/11 | 17/11 | **M4**: SSE com refund e cancelamento |
| F6 — Administração | 2 | 18/11 | 19/11 | Admin operável (stats, guests, reset) |
| F7 — Hardening | 2 | 20/11 | 23/11 | **M5**: API endurecida |
| F8 — Observabilidade | 1 | 24/11 | 24/11 | Logs e métricas completos |
| F9 — Empacotamento | 2 | 25/11 | 26/11 | Imagem + compose + shutdown |
| F10 — Conformidade | 2 | 27/11 | 30/11 | **M6**: CA-01..CA-23 verdes, `v1.0.0` |

**Buffer:** 3 dias úteis (≈ 14%) → release de risco em **03/12/2026**.

### 11.3 Cenário com 2 desenvolvedores (~16 dias úteis)

| Período | Dev A | Dev B |
|---|---|---|
| Dias 1–2 | F0 (ambos) | F0 (ambos) |
| Dias 3–4 | F1 (auth/login, JWT) | F1 (guest, rate limit) |
| Dias 5–11 | F2 → F4 → F5 | F3 → F6 → F7 |
| Dias 12–16 | F8 → F9 → F10 (ambos) | F8 → F9 → F10 (ambos) |

**Total:** ~16 dias + 2 de buffer ≈ **3,5 semanas** (release por volta de 23/11/2026).

> A paralelização depende de `F1-01` (JWT) e `F2-01` (`AIProvider`) prontos no dia 5; se atrasarem, os dois desenvolvedores convergem para o caminho crítico.

---

## 12. Definition of Done e checklist de aceitação

### 12.1 DoD por tarefa

- [ ] Código revisado e mesclado.
- [ ] Testes automatizados cobrindo o comportamento novo.
- [ ] `ruff`, `mypy` e `pytest` verdes.
- [ ] OpenAPI atualizado quando houver mudança de contrato.
- [ ] Sem TODO/FIXME pendente sem issue.
- [ ] Logs sem dados sensíveis.

### 12.2 DoD por release

- [ ] CA-01 a CA-23 verificados e rastreados.
- [ ] Cobertura ≥ 80%.
- [ ] Imagem Docker publicada e healthcheck ok.
- [ ] `README` e `.env.example` completos.
- [ ] Rodada manual com Ollama real registrada.
- [ ] Tag `v1.0.0`.

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
- [ ] CA-12 admin sem cota; guest 403 no admin
- [ ] CA-13 refund de cota em falha inicial
- [ ] CA-14 Ollama parado → 502/504/evento e resto funciona
- [ ] CA-15 troca de provedor por config (fake)
- [ ] CA-16 lockout de login
- [ ] CA-17 OpenAPI completo com exemplos
- [ ] CA-18 cobertura ≥ 80% e pipeline verde
- [ ] CA-19 headers legíveis no browser (CORS)
- [ ] CA-20 listagem + reset por `gid`
- [ ] CA-21 limites de saída com `finish_reason`
- [ ] CA-22 401 com `WWW-Authenticate`; 422 com `errors[]`
- [ ] CA-23 recusa múltiplos workers sem Redis

---

## 13. Anexos

### A. Matriz requisito → módulo → teste

| Requisito | Módulo | Teste |
|---|---|---|
| RF-AUT-01..10 | `core/auth.py`, `core/security.py`, `api/auth.py` | `tests/integration/test_auth.py` |
| RF-CHAT-01..12 | `api/chat.py`, `ai/*`, `schemas/chat.py` | `tests/integration/test_chat_*.py`, `tests/e2e/test_sse.py` |
| RF-USO-01..12 | `core/usage_store.py`, `core/rate_limit.py` | `tests/unit/test_usage_store.py`, `tests/integration/test_usage.py` |
| RF-ADM-01..05 | `api/admin.py`, `core/stats.py` | `tests/integration/test_admin.py` |
| RF-SIS-01..04 | `api/system.py`, `core/middleware.py` | `tests/integration/test_system.py` |
| RNF-SEG-* | `core/middleware.py`, `core/security.py`, `config.py` | `tests/integration/test_security.py` |
| RNF-OBS-* | `core/logging.py` | `tests/unit/test_logging.py` |
| RNF-CON-* | `ai/*`, `main.py` | `tests/integration/test_resilience.py` |
| RNF-MAN-* | estrutura + CI | pipeline |
| RNF-POR-* | `Dockerfile`, `docker-compose.yml` | build no CI |

### B. Comandos previstos (`Makefile`)

```bash
make setup        # uv sync + pre-commit install
make run          # uvicorn app.main:app --reload
make test         # pytest
make cov          # pytest --cov=app --cov-report=term-missing
make lint         # ruff check . && ruff format --check .
make type         # mypy app
make docker-build # docker build -t nexa-api .
make docker-up    # docker compose up --build
```

### C. Convenções de branch e commit

- Branches: `feat/fase-0-fundacao`, `feat/fase-5-cotas`, `fix/sse-heartbeat`…
- Commits: `feat(chat): adiciona streaming SSE`, `test(usage): cobre refund duplo`.
- Um PR por tarefa da fase; merge com squash.

### D. Pendências herdadas do SRS (§5.3)

1. Modelo e hardware finais da IA local (afeta metas de latência/concorrência).
2. Valores de cota do guest (10/dia, 3/min) a validar.
3. Limite por IP e NAT compartilhado.
4. `AI_MAX_OUTPUT_TOKENS` por papel.
5. Múltiplos nós antes do banco (obriga Redis).
6. `system` do admin permitido?
7. Proteção anti-bot na emissão de token.
8. Prazo máximo do shutdown gracioso.

> Itens 1, 2 e 4 podem bloquear a **aceitação final** (fase 10), mas não o desenvolvimento, pois são configuráveis.
