# Checklist de release — Nexa API

> Este documento preserva o release **v1.1.0** e adiciona o checklist
> **planejado** da **v2.0** (SRS v2.0, fases F12–F17).

## v1.1.0 (histórico)

| Item | Valor |
|---|---|
| Versão da API | `1.1.0` (acompanha o SRS v1.2) |
| Tag | `v1.1.0` |
| Data | 28/10/2026 |
| Escopo | Backend FastAPI sem banco (SRS v1.2) |

## Definition of Done do release (plano §12.2)

- [x] **CA-01 a CA-25 verificados e rastreados** — ver
  [`matriz-ca-testes.md`](matriz-ca-testes.md).
- [x] **Cobertura ≥ 80%** — 99,29% em `app/`, com `--cov-fail-under=80` no
  `pytest` e no CI (`coverage.xml` publicado como artefato).
- [x] **Imagem Docker e healthcheck** — `Dockerfile` multi-stage não-root,
  `HEALTHCHECK` em `/health`; `docker compose config` validado. O build/execução
  local não foi possível por falta de permissão no daemon Docker desta máquina;
  o job `docker` do CI constrói a imagem em `main`.
- [x] **`README` e `.env.example` completos** — setup, tabela de variáveis,
  exemplos `curl` (JSON/SSE), Docker/compose, shutdown gracioso e qualidade.
- [x] **Rodada manual com Ollama real registrada** — ver
  [`aceitacao-manual-2026-10-28.md`](aceitacao-manual-2026-10-28.md)
  (`qwen3-coder:latest`, Ollama 0.40.7).
- [x] **Tag `v1.1.0`** — criada no commit de entrega da F11. A tag `v1.0.0`
  permanece como marco do release anterior (SRS v1.1).

## Checklist de aceitação (SRS §4)

Todos os critérios estão marcados na matriz CA → teste. Resumo:

- [x] CA-01 boot exige credenciais válidas
- [x] CA-02 login ok / `401` genérico
- [x] CA-03 guest usa o chat sem cadastro
- [x] CA-04 SSE incremental com `done`
- [x] CA-05 JSON completo
- [x] CA-06 nada persistido, nada em log (inclusive `DEBUG`)
- [x] CA-07 cota diária → `429` + `resets_at`
- [x] CA-08 limite por minuto → `429` + `Retry-After`
- [x] CA-09 novo token não renova cota por IP
- [x] CA-10 emissão de token limitada por IP
- [x] CA-11 `/usage` coerente com os headers
- [x] CA-12 admin sem cota; guest `403` no admin
- [x] CA-13 refund de cota em falha inicial (`gid` e IP)
- [x] CA-14 Ollama parado → `502`/`504`/evento; resto funciona
- [x] CA-15 troca de provedor por configuração (fake)
- [x] CA-16 lockout de login
- [x] CA-17 OpenAPI completo com exemplos e dois `content` no chat
- [x] CA-18 cobertura ≥ 80% e pipeline verde
- [x] CA-19 headers legíveis no browser (CORS)
- [x] CA-20 listagem + reset por `gid`
- [x] CA-21 limites de saída com `finish_reason`
- [x] CA-22 `401` com `WWW-Authenticate`; `422` com `errors[]`
- [x] CA-23 recusa múltiplos workers sem Redis
- [x] CA-24 catálogo `/models` com metadados e `selectable`
- [x] CA-25 seleção de modelo por requisição, validação sem cota

## Verificação reproduzível

```bash
make lint                                          # ruff check + format
make type                                          # mypy app
make cov                                           # pytest + cobertura ≥ 80%
UPDATE_OPENAPI=1 pytest tests/integration/test_openapi_contract.py  # só se o contrato mudar
git tag -a v1.1.0 -m "Nexa API v1.1.0"
```

## Artefatos entregues

- `app/` — API completa (auth, chat JSON/SSE, cotas, admin, saúde).
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `Makefile`.
- `README.md`, `.env.example`, documentação em `docs/`.
- `.github/workflows/ci.yml` — lint, tipagem, testes/cobertura e build da imagem.
- `tests/snapshots/openapi.json` — contrato do OpenAPI versionado.

## Pendências herdadas do SRS (§5.3) e decisões

| Pendência | Situação nesta release |
|---|---|
| Modelo/hardware finais da IA local | aceitação feita com `qwen3-coder:latest`; modelo segue configurável (`OLLAMA_MODEL`) |
| Valores de cota do guest | mantidos 10/dia e 3/min por padrão, configuráveis |
| Limite por IP e NAT compartilhado | `GUEST_IP_DAILY_MESSAGES` configurável; monitoramento via `/admin/guests` |
| `AI_MAX_OUTPUT_TOKENS` por papel | valor único configurável (`AI_MAX_OUTPUT_TOKENS`) |
| Múltiplos nós antes do banco | `UVICORN_WORKERS>1` exige `REDIS_URL`; o `UsageStore` compartilhado em Redis fica para a próxima versão — manter 1 worker em produção |
| `system` do admin permitido? | desabilitado por padrão; `ALLOW_CLIENT_SYSTEM_MESSAGES=true` habilita |
| Proteção anti-bot na emissão de token | limite por IP (`GUEST_TOKENS_PER_IP_PER_HOUR`) |
| Prazo máximo do graceful shutdown | `SHUTDOWN_TIMEOUT_SECONDS` (padrão 30 s) |

## Limitações conhecidas

- Contadores de cota e estatísticas são em memória: reiniciar a API zera tudo
  (aceito pela RN-12) e a operação recomendada é um único processo.
- `AI_PROVIDER=cloud` existe apenas como contrato e falha no boot com mensagem
  clara; provedores de nuvem estão fora do escopo da v1.1.

---

## v2.0 — checklist planejado

| Item | Valor |
|---|---|
| Versão da API | `2.0.0` |
| Tag | `v2.0.0` |
| Data prevista | 02/12/2026 (com buffer) |
| Escopo | Gateway multi-provedor, compatibilidade OpenAI/Anthropic/Ollama e SQLite de usuários |
| SRS | [`srs-api-chat-ia-v1.md`](srs-api-chat-ia-v1.md) (v2.0) |
| Plano | [`plano-implementacao-api-chat-ia-v1.md`](plano-implementacao-api-chat-ia-v1.md) (v2.0) |

### Definition of Done do release v2.0

- [ ] **CA-01 a CA-47 verificados e rastreados** — ver
  [`matriz-ca-testes.md`](matriz-ca-testes.md).
- [ ] **Cobertura ≥ 80%** em `app/api`, `app/core`, `app/ai`, `app/compat` e `app/db`.
- [ ] **Benchmarks do SQLite** dentro das metas (leitura p95 ≤ 2 ms, escrita
  p95 ≤ 10 ms, listagem de 10 mil usuários p95 ≤ 20 ms).
- [ ] **Imagem Docker** com `HEALTHCHECK`, volume persistente do SQLite e
  permissões restritas.
- [ ] **`README` e `.env.example`** com todas as variáveis da v2.0.
- [ ] **Documentos especializados** publicados:
  [`provedores-ia.md`](provedores-ia.md),
  [`compatibilidade-apis-externas.md`](compatibilidade-apis-externas.md) e
  [`database-sqlite.md`](database-sqlite.md).
- [ ] **Rodada manual** com Ollama real, provedores de nuvem (fake/sandbox) e
  Claude Code.
- [ ] **Tag `v2.0.0`** criada no commit de entrega da F17.

### Checklist de aceitação v2.0

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
- [ ] CA-15 troca de provedor por config/Model ID (fake)
- [ ] CA-16 lockout de login
- [ ] CA-17 OpenAPI nativo completo com exemplos
- [ ] CA-18 cobertura ≥ 80% e pipeline verde
- [ ] CA-19 headers legíveis no browser (CORS)
- [ ] CA-20 listagem + reset por `gid`
- [ ] CA-21 limites de saída com `finish_reason`
- [ ] CA-22 401 com `WWW-Authenticate`; 422 com `errors[]`
- [ ] CA-23 recusa múltiplos workers sem Redis
- [ ] CA-24 catálogo multi-provedor com metadados e `selectable`
- [ ] CA-25 seleção de modelo por requisição, validação sem cota
- [ ] CA-26 roteamento `provider/model`
- [ ] CA-27 boot valida credenciais dos provedores
- [ ] CA-28 catálogo agregado com cache
- [ ] CA-29 capacidade incompatível → 422 sem cota
- [ ] CA-30 fallback antes do 1º token com cota única
- [ ] CA-31 OpenAI JSON
- [ ] CA-32 OpenAI streaming + `[DONE]`
- [ ] CA-33 OpenAI `/v1/models`
- [ ] CA-34 Anthropic JSON
- [ ] CA-35 Anthropic streaming + tools
- [ ] CA-36 Claude Code E2E
- [ ] CA-37 Ollama NDJSON + tags
- [ ] CA-38 auth/cotas nas rotas de compatibilidade
- [ ] CA-39 chaves nunca expostas
- [ ] CA-40 login do admin sem SQLite
- [ ] CA-41 admin inviolável (API + triggers)
- [ ] CA-42 ciclo de vida de usuários de teste
- [ ] CA-43 metas de performance do SQLite
- [ ] CA-44 migrações preservam admin + backup
- [ ] CA-45 somente usuários no banco
- [ ] CA-46 documentação consistente
- [ ] CA-47 contratos de compatibilidade verdes

### Verificação reproduzível v2.0

```bash
make lint                                          # ruff check + format
make type                                          # mypy app
make cov                                           # pytest + cobertura ≥ 80%
make bench                                         # benchmark do SQLite
UPDATE_OPENAPI=1 pytest tests/integration/test_openapi_contract.py  # se o contrato mudar
git tag -a v2.0.0 -m "Nexa API v2.0.0"
```

### Riscos e pendências da v2.0

| Pendência | Situação planejada |
|---|---|
| Conjunto inicial de modelos/provedores | definir `AI_ENABLED_PROVIDERS`, `AI_ALLOWED_MODELS` e `AI_DEFAULT_MODEL` |
| Cota de usuários de teste | `TEST_USER_DEFAULT_DAILY_MESSAGES` / `TEST_USER_DEFAULT_RATE_PER_MIN` |
| Fallback por padrão | `AI_FALLBACK_CHAIN` vazio (desabilitado) até decisão |
| Múltiplos nós | Redis obrigatório para cotas; SQLite continua local |
| API key na compatibilidade Ollama | `COMPAT_OLLAMA_ALLOW_LOCALHOST=false` em produção |
| Retenção de usuários de teste | `TEST_USER_TTL_HOURS=168` (7 dias) |
| `count_tokens` | contagem do provedor quando disponível; estimativa documentada |
| Prazo de shutdown | `SHUTDOWN_TIMEOUT_SECONDS=30` |

### Limitações conhecidas da v2.0

- O SQLite armazena apenas usuários; não há histórico de conversas.
- As API keys de compatibilidade vivem em variável de ambiente, sem gestão pelo
  admin.
- Ferramentas são executadas pelo cliente; a API apenas traduz `tool_use`.
- Embeddings, fine-tuning, batches e arquivos estão fora do escopo.
- O fallback não ocorre depois do primeiro token.
