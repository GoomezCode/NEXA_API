# Especificação de Requisitos de Software (SRS)

## API do Chat de IA via Web — Gateway multi-provedor e compatibilidade

| Campo | Valor |
|---|---|
| **Projeto** | Chat de IA via Web: Backend (API) |
| **Documento** | SRS: Especificação de Requisitos de Software |
| **Versão** | 2.0 (especificação para implementação) |
| **Data** | 29/10/2026 |
| **Padrões de referência** | IEEE Std 830-1998, ISO/IEC/IEEE 29148:2018 |
| **Documento de origem** | `levantamento-chat-ia-web.md` (documento externo a este repositório) |
| **Estado** | **F16 CONCLUIDO** - compatibilidade Ollama (`/api/chat`, `/api/tags`, `/api/show`, `/api/ps`) implementada. Próxima: F17 (hardening, performance e release v2.0.0). |

### Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 2.0 | 29/10/2026 | Gateway multi-provedor (Ollama local, Ollama Cloud, OpenAI, Anthropic, Gemini e OpenAI-compatible); camada de compatibilidade OpenAI, Anthropic (Claude Code) e Ollama; SQLite exclusivo para usuários (ADMIN permanente e usuários de teste descartáveis); metas de performance do banco; novos critérios CA-26..CA-47 |
| 1.2 | 28/10/2026 | Catálogo de modelos locais (`GET /models`) e seleção opcional do modelo por requisição de chat; guest passa a escolher o modelo (RN-13 revisada), mantendo proibidos `system` e parâmetros de geração; allowlist opcional (`AI_ALLOWED_MODELS`) e cache curto do catálogo (`MODELS_CACHE_SECONDS`); novos critérios CA-24/CA-25 |
| 1.1 | 28/10/2026 | Revisão de consistência após análise do SRS: contrato de SSE explicitado (consumo via `fetch`, não `EventSource`); limite de tokens de saída; descoberta e reset de `gid` no admin; semântica única de cota e de `usage`; schemas de `/admin/*` e `/health*`; variáveis de ambiente faltantes; separação `AI_PROVIDER`/`CLOUD_PROVIDER`; CORS com `expose_headers`; remoção da exceção de log de conteúdo; prioridades alinhadas aos critérios de aceitação |
| 1.0 | 28/10/2026 | Primeira versão do SRS da API, **sem banco de dados**. Existia um único usuário salvo (**ADMIN**, com senha, vindo da configuração) e usuários **ghost** (anônimos) com limite de uso. A persistência ficava para a próxima versão da API |

> **Resumo da versão atual:** a API deixa de ser um cliente de um único provedor e passa a ser um **gateway de IA multi-provedor**, com roteamento por `provider/model`, catálogo agregado e fallback controlado. Além da API nativa (`/api/v1`), expõe **camadas de compatibilidade** com OpenAI (`/v1`), Anthropic/Claude Code (`/v1/messages`) e Ollama (`/api`). O **SQLite** passa a existir, mas **exclusivamente para usuários**: um **ADMIN permanente e inviolável** e **usuários de teste descartáveis**. O chat continua **efêmero**: nenhuma mensagem, pergunta ou resposta é persistida. As cotas e o rate limit continuam em memória (ou Redis, quando configurado).

---

## Sumário

1. [Introdução](#1-introdução)
2. [Descrição geral](#2-descrição-geral)
3. [Requisitos específicos](#3-requisitos-específicos)
   - 3.1 [Interfaces externas](#31-interfaces-externas)
   - 3.2 [Requisitos funcionais](#32-requisitos-funcionais)
   - 3.3 [Requisitos de estado e configuração](#33-requisitos-de-estado-e-configuração)
   - 3.4 [Requisitos não funcionais](#34-requisitos-não-funcionais)
   - 3.5 [Regras de negócio](#35-regras-de-negócio)
   - 3.6 [Restrições de projeto](#36-restrições-de-projeto)
   - 3.7 [Catálogo de endpoints](#37-catálogo-de-endpoints)
   - 3.8 [Padrão de erros](#38-padrão-de-erros)
4. [Critérios de aceitação](#4-critérios-de-aceitação)
5. [Apêndices](#5-apêndices)

---

## 1. Introdução

### 1.1 Propósito

Este documento especifica os requisitos da **API de backend** de um sistema de chat de IA via web na versão **2.0**. Ele cobre a evolução para um **gateway multi-provedor**, as **camadas de compatibilidade** com APIs de mercado e o **banco SQLite de usuários**, além de preservar os requisitos da v1.2 que continuam válidos. Destina-se a desenvolvedores, testadores e demais envolvidos no projeto, e serve de base para o projeto técnico, a implementação, os testes e a validação da API.

### 1.2 Escopo

O produto especificado é a **API REST** (em Python/FastAPI) que:

- autentica o **administrador** (usuário permanente, definido na configuração e espelhado no SQLite);
- autentica **usuários de teste** criados rapidamente pelo administrador no SQLite;
- permite o uso do chat por **usuários guest** (anônimos, também chamados de ghost), com **limites de uso**;
- recebe perguntas e devolve respostas de **múltiplos provedores de IA**, com **streaming**;
- roteia requisições por **`provider/model`**, agrega catálogos e aplica fallback controlado;
- expõe **APIs compatíveis** com OpenAI, Anthropic (Claude Code) e Ollama, para que sistemas externos usem a NEXA API como se fosse o provedor original;
- oferece ao administrador consultas de monitoramento (estatísticas em memória, status dos provedores e status do banco);
- mantém o chat **efêmero**, sem persistir conteúdo de conversas.

**Fora do escopo desta versão:** o frontend React, a infraestrutura de hospedagem, a escolha e o treinamento dos modelos, faturamento, histórico de conversas, feedback, verificação de e-mail, recuperação de senha, refresh token, auditoria persistente, RAG, embeddings e upload de arquivos.

### 1.3 Definições, siglas e abreviações

| Termo | Definição |
|---|---|
| **API** | Interface de programação de aplicações |
| **REST** | Estilo arquitetural para APIs sobre HTTP |
| **SRS** | Software Requirements Specification |
| **JWT** | JSON Web Token, token assinado usado para autenticação |
| **SSE** | Server-Sent Events, fluxo de eventos do servidor para o cliente sobre HTTP |
| **NDJSON** | Newline-Delimited JSON, um objeto JSON por linha (usado pelo Ollama) |
| **Provedor de IA** | Serviço concreto que executa um modelo (Ollama local, Ollama Cloud, OpenAI, Anthropic, Google, OpenAI-compatible) |
| **Adapter** | Implementação que traduz o contrato interno `AIProvider` para o protocolo de um provedor |
| **Gateway** | Camada que recebe a requisição, resolve o provedor/modelo e encaminha a chamada |
| **Strategy** | Padrão de projeto que permite trocar algoritmos/implementações via interface comum |
| **Model ID** | Identificador canônico no formato `provider/model` (ex.: `anthropic/claude-sonnet-4-5`) |
| **Alias** | Nome amigável que resolve para um Model ID (ex.: `smart`) |
| **Compatibilidade** | Camada que expõe o contrato de outra API (OpenAI, Anthropic ou Ollama) sobre o núcleo NEXA |
| **API key (NEXA)** | Segredo `nxa_...` usado por sistemas externos nas rotas de compatibilidade |
| **Admin** | Único usuário permanente; credenciais definidas por variáveis de ambiente e espelhadas no SQLite |
| **Usuário de teste** | Usuário criado rapidamente pelo admin, com papel `user`, descartável e sem privilégios administrativos |
| **Ghost / Guest** | Usuário anônimo, sem cadastro nem senha, identificado por um token de convidado |
| **Cota efetiva** | Menor valor entre a cota restante por `gid`/`uid` e a cota restante por IP (RF-USO-04, RF-USO-12) |
| **Unidade de cota** | Uma requisição de chat consome exatamente 1 unidade, independentemente do número de mensagens (RF-USO-11) |
| **Token de convidado** | JWT emitido a um guest por `POST /auth/guest` |
| **SQLite / WAL** | Banco embarcado em arquivo e seu modo de journaling Write-Ahead Logging |
| **ADMIN protegido** | Linha do admin com `is_protected = 1`, imune a exclusão, rebaixamento e desativação |
| **RF / RNF / RN / RD** | Requisito Funcional / Não Funcional / Regra de Negócio / Requisito de Dados |

**Prioridades (MoSCoW):** **M** = Deve (Must), **S** = Deveria (Should), **C** = Poderia (Could).

### 1.4 Referências

- `levantamento-chat-ia-web.md`: documento de planejamento do projeto.
- IEEE Std 830-1998: *Recommended Practice for Software Requirements Specifications*.
- ISO/IEC/IEEE 29148:2018: *Requirements engineering*.
- RFC 7519 (JWT), RFC 6750 (Bearer Token), RFC 9457 (Problem Details for HTTP APIs).
- OpenAI API Reference (`/v1/chat/completions`, `/v1/models`).
- Anthropic Messages API (`/v1/messages`, `/v1/messages/count_tokens`).
- Ollama API (`/api/chat`, `/api/tags`, `/api/show`).
- Google Gemini API (`generateContent` e `streamGenerateContent`).
- Documentação do FastAPI, do SQLite e do Ollama.

### 1.5 Visão geral do documento

A seção 2 descreve o produto e seu contexto. A seção 3 traz os requisitos detalhados, incluindo gateway, compatibilidade e banco. A seção 4 define os critérios de aceitação e a seção 5 reúne apêndices, o mapa de migração da v1.1.0 para a v2.0 e o que fica para a próxima versão.

---

## 2. Descrição geral

### 2.1 Perspectiva do produto

A API é o componente central do sistema e fica entre os clientes e os provedores de IA:

```
Frontend próprio ─┐
Claude Code ──────┤   /api/v1/*        (nativa, JWT)
Cursor/LangChain ─┼─► /v1/*            (compatível OpenAI, API key)
Clientes Ollama ──┤   /api/*           (compatível Ollama, API key)
Sistemas internos ┘   /v1/messages     (compatível Anthropic, API key)
                              │
                     NEXA API (FastAPI)
                     ├── Router de modelos (provider/model, aliases, fallback)
                     ├── Cotas e rate limit (memória ou Redis)
                     ├── SQLite (usuários; ADMIN permanente)
                     └── Adapters de IA
                          ├── ollama (local)
                          ├── ollama_cloud
                          ├── openai
                          ├── anthropic
                          ├── google (Gemini)
                          └── openai_compatible (Groq, OpenRouter, vLLM, LM Studio...)
```

O frontend e os sistemas externos **nunca** se comunicam diretamente com a IA. Somente a API o faz, o que garante proteção das chaves, controle de acesso, limites de uso e independência do cliente em relação ao modelo usado.

**Persistência:** o SQLite guarda **apenas usuários**. O histórico da conversa continua sendo responsabilidade do cliente, que reenvia o contexto a cada requisição. Nenhum conteúdo de mensagem é gravado.

### 2.2 Funções do produto (resumo)

| Módulo | Funções principais |
|---|---|
| **Autenticação** | Login do admin e de usuários de teste; emissão de token de convidado; chaves de API para compatibilidade |
| **Chat** | Enviar mensagens e receber resposta completa ou em streaming (efêmero, sem persistência) |
| **Gateway de IA** | Roteamento `provider/model`, aliases, allowlist, fallback e catálogo agregado |
| **Compatibilidade** | Contratos OpenAI, Anthropic (Claude Code) e Ollama sobre o núcleo NEXA |
| **Limites de uso** | Cota diária e limite por minuto para guest; limites para usuários de teste; limites por API key |
| **Administração** | Estatísticas em memória, status dos provedores, guests, reset de cota e gestão de usuários de teste |
| **Sistema** | Health check, prontidão, status do banco e documentação automática |

### 2.3 Classes de usuários e características

| Classe | Descrição | Permissões |
|---|---|---|
| **Guest** (ghost) | Visitante anônimo com token de convidado | Chat, consulta do próprio uso, **sujeito a limites de uso** |
| **Usuário de teste** | Usuário criado pelo admin no SQLite, papel `user`, descartável | Chat e consulta do próprio uso, com limites configuráveis; **sem acesso ao admin** |
| **Administrador** | Autenticado com usuário e senha, papel `admin`, permanente | Tudo do usuário de teste, **sem cota diária** (mantém limites técnicos) + endpoints `/admin/*` |
| **Sistema externo** | Aplicação que usa as rotas de compatibilidade com uma **API key NEXA** | Chat e catálogo, sujeito a cotas, rate limit e allowlist de modelos; **sem acesso ao admin** |
| **Cliente de API (frontend)** | Aplicação React que consome a API nativa em nome do usuário | Mesmas permissões de quem representa |

### 2.4 Ambiente operacional

- **Linguagem/runtime:** Python 3.12 (mínimo 3.11).
- **Framework:** FastAPI (ASGI, executado com Uvicorn).
- **IA local:** Ollama (padrão `http://localhost:11434`).
- **IA em nuvem:** OpenAI, Anthropic, Google Gemini, Ollama Cloud e provedores OpenAI-compatible.
- **Armazenamento:** SQLite em arquivo local (padrão `./data/nexa.db`), com WAL. Contadores de uso ficam em memória (nó único e **processo único**) ou em Redis (vários nós/processos, ver RP-07).
- **Sistemas operacionais:** Linux (produção); Windows, macOS e Linux (desenvolvimento).

### 2.5 Restrições gerais

- Backend obrigatoriamente em **Python/FastAPI**.
- **SQLite é o único banco** nesta versão e armazena **somente usuários**. Não há histórico de conversas, mensagens, auditoria persistente ou billing.
- O **ADMIN é permanente e inviolável**: sobrevive a limpezas, migrações e até à perda do arquivo do banco (fallback para a configuração).
- Troca de provedor e de modelo é feita por **configuração e por requisição**, sem alterar as rotas.
- Modelos de IA **não possuem memória**: o contexto é enviado a cada requisição.
- Desempenho da IA local depende do hardware (CPU/GPU/VRAM) disponível.
- Contadores em memória são **voláteis**: reiniciar a API zera os limites de uso (ver RN-12).
- Os contadores em memória exigem **um único processo** (`UVICORN_WORKERS=1`); sem Redis, a API recusa múltiplos processos (RP-07, RNF-CON-06).
- O streaming nativo exige `fetch`/`ReadableStream` no cliente; `EventSource` não é compatível com `POST` + `Authorization` (RP-09).
- As rotas de compatibilidade são **traduções sem estado**: não alteram o núcleo nem persistem conteúdo.

### 2.6 Premissas e dependências

- O provedor escolhido está acessível pela rede e com o modelo configurado disponível.
- O frontend consome a API via HTTPS em produção.
- O frontend envia a **lista completa de mensagens** da conversa a cada requisição de chat e guarda o histórico por conta própria.
- As credenciais do administrador, o segredo do JWT e as chaves dos provedores são fornecidas por variáveis de ambiente no deploy.
- O arquivo SQLite fica em volume persistente e com permissão restrita ao processo da API.
- Sistemas externos que usam as rotas de compatibilidade recebem uma **API key NEXA** emitida pelo operador (variável de ambiente).
- Em produção, modelo local exige servidor com GPU, o que pode tornar a nuvem mais econômica.

---

## 3. Requisitos específicos

### 3.1 Interfaces externas

#### 3.1.1 Interfaces com o cliente (API HTTP)

| Aspecto | Especificação |
|---|---|
| Protocolo | HTTP/1.1 ou superior; HTTPS obrigatório em produção |
| Estilo nativo | REST, JSON (`application/json`, UTF-8), campos em `snake_case` e em inglês |
| Prefixos | Nativo `/api/v1`; OpenAI `/v1`; Anthropic `/v1`; Ollama `/api` |
| Streaming nativo | SSE (`text/event-stream`) com `Cache-Control: no-cache`, `Connection: keep-alive` e `X-Accel-Buffering: no`; heartbeat `: ping` a cada 15 s de inatividade |
| Streaming compatível | OpenAI: SSE `data:` + `data: [DONE]`; Anthropic: eventos SSE tipados; Ollama: NDJSON |
| Autenticação nativa | Header `Authorization: Bearer <JWT>` |
| Autenticação compatível | `Authorization: Bearer nxa_...` (OpenAI e Ollama) e `x-api-key: nxa_...` (Anthropic); JWT também é aceito |
| Documentação | OpenAPI/Swagger nativo em `/docs` e ReDoc em `/redoc`; contratos de compatibilidade em `docs/compatibilidade-apis-externas.md` e OpenAPI próprio em `/v1/openapi.json` e `/api/openapi.json` (S) |
| Datas e horas | ISO 8601 em UTC (ex.: `2026-10-29T14:30:00Z`) |
| Rastreabilidade | `X-Request-ID` em todas as respostas (RF-SIS-03), inclusive nas rotas de compatibilidade |
| CORS | Origens de `CORS_ORIGINS`, com `expose_headers` para `X-RateLimit-*`, `Retry-After`, `X-Request-ID` e `Content-Type` (RNF-SEG-04) |

**Nota sobre o streaming no cliente nativo:** o endpoint de chat é `POST` e exige `Authorization`, portanto **não pode ser consumido com `EventSource`**. O frontend deve usar `fetch` com `ReadableStream` sobre `response.body` e interpretar o formato SSE descrito em 3.7.3.

**Nota sobre a coexistência de prefixos:** `/api/v1/*` (nativo) não colide com `/api/chat` e `/api/tags` (compatível Ollama). `/v1/chat/completions`, `/v1/models` e `/v1/messages` compartilham o prefixo `/v1` sem colisão entre si.

#### 3.1.2 Interface com provedores de IA

- Toda comunicação com IA passa pela interface abstrata `AIProvider`, cujo retorno inclui o texto, o `finish_reason` (`stop`, `length`, `timeout`, `tool_use`), os blocos de ferramenta e, quando o provedor informar, a contagem de tokens.
- O **`ProviderRouter`** resolve o Model ID (`provider/model`), aplica aliases/allowlist, verifica capacidades e escolhe o adapter.
- Cada adapter é **stateless** e recebe credenciais, base URL e timeouts da configuração.
- Formato interno de mensagens: `[{"role": "system" | "user" | "assistant" | "tool", "content": "...", "tool_calls": [...]}]`.
- Os limites `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS` são aplicados pela API, independentemente do provedor (RF-CHAT-12).
- Falhas de provedor são normalizadas em `ProviderError`, `ProviderUnavailable`, `ProviderTimeout` e `ProviderConfigurationError`, e traduzidas para o envelope da superfície chamadora.

**Matriz de provedores e protocolos**

| Provedor | ID | Protocolo principal | Streaming | Catálogo |
|---|---|---|---|---|
| Ollama local | `ollama` | `POST {OLLAMA_URL}/api/chat` (NDJSON) | ✅ | `GET /api/tags` |
| Ollama Cloud | `ollama_cloud` | `POST {OLLAMA_CLOUD_URL}/api/chat` (NDJSON) + Bearer | ✅ | `GET /api/tags` |
| OpenAI | `openai` | `POST {OPENAI_BASE_URL}/chat/completions` (SSE) | ✅ | `GET /models` |
| Anthropic | `anthropic` | `POST {ANTHROPIC_BASE_URL}/v1/messages` (SSE) | ✅ | `GET /v1/models` |
| Google Gemini | `google` | `POST {GOOGLE_BASE_URL}/v1beta/models/{model}:streamGenerateContent?alt=sse` | ✅ | `GET /v1beta/models` |
| OpenAI-compatible | `openai_compatible` | `POST {OPENAI_COMPAT_BASE_URL}/chat/completions` (SSE) | ✅ | `GET /models` |
| Fake (testes) | `fake` | Em processo, determinístico | ✅ | Estático |

**Capacidades mínimas por adapter**

| Capacidade | `ollama` | `ollama_cloud` | `openai` | `anthropic` | `google` | `openai_compatible` |
|---|---|---|---|---|---|---|
| `chat` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `stream` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tools` | ✅* | ✅* | ✅ | ✅ | ✅ | ✅* |
| `vision` | ✅* | ✅* | ✅ | ✅ | ✅ | ✅* |
| `json_mode` | ✅* | ✅* | ✅ | ⚠️** | ✅ | ✅* |

`*` Depende do modelo escolhido; a API valida a capacidade declarada pelo adapter e, quando possível, pelo catálogo do provedor.
`**` Anthropic não possui `response_format` nativo; a API usa instrução de sistema e validação de saída.

#### 3.1.3 Interfaces de hardware

Não há interface direta de hardware. O desempenho da IA local depende de CPU (16 GB de RAM para modelos de 1B a 8B parâmetros) e, preferencialmente, de GPU. O SQLite depende de disco local com escrita estável; volumes de rede não são recomendados.

#### 3.1.4 Configuração (variáveis de ambiente)

**Núcleo e segurança**

| Variável | Descrição | Exemplo |
|---|---|---|
| `ENVIRONMENT` | `development` ou `production` | `development` |
| `LOG_LEVEL` | Nível de log | `INFO` |
| `ADMIN_USERNAME` | Nome do administrador permanente | `admin` |
| `ADMIN_PASSWORD_HASH` | Hash argon2id/bcrypt do admin | (gerado no deploy) |
| `JWT_SECRET` | Segredo de assinatura do JWT | (valor aleatório forte) |
| `JWT_ALGORITHM` | Algoritmo de assinatura | `HS256` |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | Validade do token do admin | `60` |
| `GUEST_TOKEN_EXPIRE_HOURS` | Validade do token de convidado | `24` |
| `CORS_ORIGINS` | Origens permitidas | `http://localhost:5173` |
| `TRUSTED_PROXIES` | IPs/CIDRs de proxies confiáveis | `127.0.0.1/32` |
| `MAX_REQUEST_BODY_BYTES` | Tamanho máximo do corpo | `2097152` |
| `UVICORN_WORKERS` | Nº de processos; **deve ser `1`** sem Redis | `1` |
| `SHUTDOWN_TIMEOUT_SECONDS` | Prazo do graceful shutdown | `30` |

**Cotas e limites**

| Variável | Descrição | Exemplo |
|---|---|---|
| `GUEST_DAILY_MESSAGES` | Cota diária por `gid` | `10` |
| `GUEST_RATE_PER_MIN` | Limite por minuto por `gid` | `3` |
| `GUEST_MAX_MESSAGES_PER_REQUEST` | Máx. de mensagens no contexto (guest) | `20` |
| `GUEST_MAX_MESSAGE_CHARS` | Máx. de caracteres por mensagem (guest) | `2000` |
| `GUEST_IP_DAILY_MESSAGES` | Cota diária somada por IP | `30` |
| `GUEST_TOKENS_PER_IP_PER_HOUR` | Tokens de convidado emitidos por IP/hora | `10` |
| `RATE_LIMIT_ADMIN_CHAT_PER_MIN` | Limite técnico de chat do admin | `60` |
| `MAX_MESSAGES_PER_REQUEST` | Máx. de mensagens no contexto (admin) | `100` |
| `MAX_MESSAGE_CHARS` | Máx. de caracteres por mensagem (admin) | `8000` |
| `ALLOW_CLIENT_SYSTEM_MESSAGES` | Aceita mensagens `system` do cliente (RN-06) | `false` |
| `DEFAULT_SYSTEM_PROMPT` | Prompt de sistema padrão do servidor | (texto) |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | Tentativas de login / duração do bloqueio | `5` / `15` |
| `REDIS_URL` | (Opcional) Redis para contadores compartilhados | (vazio) |

**Gateway de IA**

| Variável | Descrição | Exemplo |
|---|---|---|
| `AI_DEFAULT_MODEL` | Model ID padrão quando o chat omite `model` | `ollama/llama3.1` |
| `AI_MODEL_ALIASES` | JSON de aliases → Model ID | `{"fast":"ollama/llama3.1"}` |
| `AI_ALLOWED_MODELS` | Allowlist com globs, separada por vírgulas; vazio = todos | `ollama/*,openai/gpt-4o-mini` |
| `AI_FALLBACK_CHAIN` | Lista de Model IDs tentados antes do primeiro token | `openai/gpt-4o-mini,ollama/llama3.1` |
| `AI_ENABLED_PROVIDERS` | Provedores habilitados; vazio = autodetectar por credencial | `ollama,openai,anthropic` |
| `AI_TIMEOUT_SECONDS` | Timeout de chamadas não-streaming | `120` |
| `AI_STREAM_IDLE_TIMEOUT_SECONDS` | Inatividade máxima no streaming | `60` |
| `AI_MAX_OUTPUT_TOKENS` | Máx. de tokens de saída (servidor) | `1024` |
| `AI_MAX_OUTPUT_SECONDS` | Tempo máximo de geração | `300` |
| `AI_MAX_RETRIES` | Tentativas de fallback antes do primeiro token | `1` |
| `MODELS_CACHE_SECONDS` | TTL do cache do catálogo; `0` desativa | `10` |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Endpoint e modelo local (compatibilidade com v1) | `http://localhost:11434` / `llama3.1` |
| `OLLAMA_API_KEY` | (Opcional) chave para Ollama protegido | (vazio) |
| `OLLAMA_CLOUD_URL` / `OLLAMA_CLOUD_API_KEY` | Endpoint e chave do Ollama Cloud | `https://ollama.com` / (chave) |
| `OLLAMA_CLOUD_DEFAULT_MODEL` | Modelo padrão do Ollama Cloud | `qwen3-coder:480b` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | Chave e base URL da OpenAI | (chave) / `https://api.openai.com/v1` |
| `OPENAI_DEFAULT_MODEL` | Modelo padrão da OpenAI | `gpt-4o-mini` |
| `OPENAI_ORG_ID` | (Opcional) organização enviada no header `OpenAI-Organization` | (vazio) |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | Chave e base URL da Anthropic | (chave) / `https://api.anthropic.com` |
| `ANTHROPIC_VERSION` | Versão do header `anthropic-version` | `2023-06-01` |
| `ANTHROPIC_DEFAULT_MODEL` | Modelo padrão da Anthropic | `claude-sonnet-4-5` |
| `GOOGLE_API_KEY` / `GOOGLE_BASE_URL` | Chave e base URL do Gemini | (chave) / `https://generativelanguage.googleapis.com` |
| `GOOGLE_DEFAULT_MODEL` | Modelo padrão do Gemini | `gemini-2.5-flash` |
| `OPENAI_COMPAT_NAME` | Nome lógico do provedor genérico | `groq` |
| `OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_API_KEY` | Endpoint e chave do provedor genérico | `https://api.groq.com/openai/v1` / (chave) |
| `OPENAI_COMPAT_DEFAULT_MODEL` | Modelo padrão do provedor genérico | `llama-3.3-70b-versatile` |

**Banco de dados (SQLite)**

| Variável | Descrição | Exemplo |
|---|---|---|
| `DATABASE_URL` | URL do SQLite; apenas `sqlite:///` é aceito | `sqlite:///./data/nexa.db` |
| `SQLITE_WAL` | Habilita WAL | `true` |
| `SQLITE_SYNCHRONOUS` | `NORMAL` (recomendado) ou `FULL` | `NORMAL` |
| `SQLITE_BUSY_TIMEOUT_MS` | Espera por lock de escrita | `5000` |
| `SQLITE_CACHE_SIZE_KB` | Cache de páginas | `65536` |
| `SQLITE_MMAP_BYTES` | Tamanho do mmap | `268435456` |
| `SQLITE_READ_POOL_SIZE` | Conexões de leitura | `4` |
| `USERS_CACHE_TTL_SECONDS` | TTL do cache de autenticação | `30` |
| `USERS_CACHE_MAX_ENTRIES` | Teto do cache de usuários | `10000` |
| `ALLOW_TEST_USERS` | Habilita criação de usuários de teste | `true` |
| `TEST_USER_DEFAULT_DAILY_MESSAGES` | Cota diária padrão do usuário de teste | `100` |
| `TEST_USER_DEFAULT_RATE_PER_MIN` | Limite por minuto padrão | `10` |
| `TEST_USER_TTL_HOURS` | Expiração padrão de usuário de teste; `0` = sem expiração | `168` |
| `TEST_USER_MAX_BULK` | Máximo de usuários por criação em lote | `100` |
| `DB_BACKUP_BEFORE_MIGRATION` | Backup antes de migrar | `true` |
| `DB_BACKUP_DIR` | Diretório dos backups | `./data/backups` |
| `DB_STARTUP_CHECK` | Verificação de integridade no boot (`quick`/`full`/`off`) | `quick` |
| `DB_OPTIMIZE_ON_SHUTDOWN` | Executa `PRAGMA optimize` ao encerrar | `true` |

**Compatibilidade**

| Variável | Descrição | Exemplo |
|---|---|---|
| `COMPAT_OPENAI_ENABLED` | Habilita `/v1/chat/completions` e `/v1/models` | `true` |
| `COMPAT_ANTHROPIC_ENABLED` | Habilita `/v1/messages` e `count_tokens` | `true` |
| `COMPAT_OLLAMA_ENABLED` | Habilita `/api/chat`, `/api/tags` e `/api/show` | `true` |
| `COMPAT_API_KEYS` | Chaves NEXA separadas por vírgula | `nxa_live_...,nxa_test_...` |
| `COMPAT_API_KEY_RATE_PER_MIN` | Limite por minuto por API key | `60` |
| `COMPAT_ALLOW_ANONYMOUS` | Permite compatibilidade sem chave (somente desenvolvimento) | `false` |
| `COMPAT_OLLAMA_ALLOW_LOCALHOST` | Permite clientes Ollama locais sem chave (somente desenvolvimento) | `false` |
| `COMPAT_ANTHROPIC_VERSION` | Versão mínima aceita em `anthropic-version` | `2023-06-01` |

**Variáveis descontinuadas:** `AI_PROVIDER`, `CLOUD_PROVIDER`, `CLOUD_BASE_URL`, `CLOUD_MODEL` e `CLOUD_API_KEY` deixam de existir na v2.0. Na primeira inicialização, a API as converte em `AI_DEFAULT_MODEL=ollama/<OLLAMA_MODEL>` (quando `AI_PROVIDER=ollama`) e emite um aviso de depreciação no log. A conversão não é aplicada em produção após a fase F17.

Toda a configuração deve ser validada na inicialização (tipo, faixa e obrigatoriedade), e a API deve **recusar-se a iniciar** com valores inválidos, além das regras específicas de RF-AUT-01, RF-GTW-03 e RNF-SEG-12.

---

### 3.2 Requisitos funcionais

#### 3.2.1 Autenticação (RF-AUT)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-AUT-01** | Na inicialização, a API deve carregar `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` e **recusar-se a iniciar** se qualquer um estiver ausente ou se o hash não for válido. O admin é o único usuário permanente do sistema. | M |
| **RF-AUT-02** | A API deve permitir **login** com `username` e `password` para o admin (credenciais da configuração) e para usuários de teste (SQLite), retornando um **access token JWT**, o tipo do token, o tempo de expiração e o papel. | M |
| **RF-AUT-03** | A senha nunca deve ser mantida em texto puro: a verificação compara a senha recebida com o hash (`argon2id` ou `bcrypt`) em tempo constante. | M |
| **RF-AUT-04** | Credenciais inválidas devem retornar `401 Unauthorized` com mensagem genérica, sem revelar se o usuário existe. O tempo de resposta deve ser equivalente para usuário inexistente e senha incorreta. | M |
| **RF-AUT-05** | A API deve oferecer **emissão de token de convidado** (`POST /auth/guest`), sem credenciais. Cada chamada gera um `gid` (UUID v4) e um JWT com papel `guest`. | M |
| **RF-AUT-06** | O access token JWT deve conter, no mínimo, `sub` (nome do admin, `uid` do usuário de teste ou `gid` do guest), `role` (`admin`, `user` ou `guest`), `iat` e `exp`. | M |
| **RF-AUT-07** | Todos os endpoints, exceto os públicos (login, token de convidado, health, documentação e rotas de compatibilidade autenticadas por API key), devem exigir token válido e não expirado. | M |
| **RF-AUT-08** | A emissão de tokens de convidado deve ser limitada por IP (`GUEST_TOKENS_PER_IP_PER_HOUR`), retornando `429` com o código `guest_token_limit_reached` e `Retry-After` ao exceder. | M |
| **RF-AUT-09** | Nesta versão não há refresh token nem revogação no servidor: o **logout** é feito pelo cliente descartando o token. Tokens do admin e de usuários de teste têm validade curta e configurável. | S |
| **RF-AUT-10** | Tokens com assinatura inválida, expirados ou com papel desconhecido devem retornar `401` com `WWW-Authenticate: Bearer` (RFC 6750). | M |
| **RF-AUT-11** | As rotas de compatibilidade devem aceitar uma **API key NEXA** (`nxa_...`) em `Authorization: Bearer` ou `x-api-key`, comparada em tempo constante. Chave ausente/inválida retorna `401` no envelope da superfície chamada. | M |
| **RF-AUT-12** | Usuários de teste devem autenticar com usuário e senha e receber JWT com papel `user`; eles não podem acessar `/admin/*` nem receber privilégios de admin. | M |
| **RF-AUT-13** | A API key é um segredo de operação: só pode ser lida de `COMPAT_API_KEYS`, nunca é retornada e apenas o prefixo/fingerprint aparece em log. | M |

#### 3.2.2 Chat nativo (RF-CHAT)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-CHAT-01** | A API deve expor endpoint de chat que recebe a **lista completa de mensagens** (`messages`) e devolve a resposta do modelo. | M |
| **RF-CHAT-02** | O endpoint deve suportar dois modos, controlados por `stream` (padrão `true`): **streaming via SSE** e **resposta completa em JSON**. | M |
| **RF-CHAT-03** | No modo streaming, a resposta deve ser enviada em eventos SSE incrementais, com evento final de conclusão e evento de erro quando aplicável (3.7.3), além de `Content-Type: text/event-stream`, cabeçalhos de cache/proxy e heartbeat `: ping` a cada 15 s. | M |
| **RF-CHAT-04** | O endpoint deve validar a entrada: `messages` não vazio; cada item com `role` ∈ {`system`, `user`, `assistant`, `tool`} e `content` não vazio (desconsiderando espaços); a última mensagem deve ter `role = user`; respeitar os limites de mensagens e caracteres do papel. | M |
| **RF-CHAT-05** | O endpoint de chat **não deve conhecer o provedor concreto**: deve usar o `ProviderRouter` e a interface `AIProvider`. | M |
| **RF-CHAT-06** | O chat é **efêmero**: nenhuma mensagem, pergunta ou resposta é gravada pela API (nem em arquivo, nem no SQLite, nem em log). | M |
| **RF-CHAT-07** | Se o cliente interromper a conexão durante o streaming, a API deve cancelar a requisição ao provedor e liberar recursos. Se a interrupção ocorrer antes do primeiro trecho, aplica-se a devolução de cota (RF-USO-05). | S |
| **RF-CHAT-08** | Falhas do provedor devem ser traduzidas para `502` (`ai_provider_error`) ou `504` (`ai_provider_timeout`); se o streaming já iniciou, para um evento SSE `error` com o mesmo `code`. | M |
| **RF-CHAT-09** | O endpoint **não** deve aceitar `system_prompt` nem parâmetros de geração enviados pelo cliente. A orientação do sistema é feita por mensagens `role = system` (RN-06), e os parâmetros de geração são definidos pelo servidor. | M |
| **RF-CHAT-10** | As mensagens `system` do cliente e o prompt padrão são usados apenas no contexto enviado à IA. | M |
| **RF-CHAT-11** | Antes de chamar o provedor, o endpoint deve aplicar os limites de uso do chamador (3.2.3). Requisição barrada por limite **não** chega ao provedor. | M |
| **RF-CHAT-12** | A geração deve ser limitada por `AI_MAX_OUTPUT_TOKENS` e `AI_MAX_OUTPUT_SECONDS`. Ao atingir qualquer limite, a resposta termina normalmente com `finish_reason` = `length` ou `timeout`, tanto em JSON quanto no evento `done`. | M |
| **RF-CHAT-13** | A API deve expor `GET /api/v1/models` (autenticado) listando os modelos de **todos os provedores habilitados**, com provedor, nome, metadados, capacidades e `selectable`, além do modelo padrão. | M |
| **RF-CHAT-14** | O chat deve aceitar o campo opcional `model` no formato `provider/model` (ou alias). Omitido, usa `AI_DEFAULT_MODEL`. O modelo deve existir no provedor e ser permitido por `AI_ALLOWED_MODELS`; caso contrário, retorna `422` com `errors[]`, sem consumir cota. | M |
| **RF-CHAT-15** | O chat nativo deve aceitar `tools` e `tool_choice` opcionais, repassando-os ao provedor quando a capacidade `tools` estiver disponível; a resposta pode conter `tool_calls` e `finish_reason = tool_use`. | S |
| **RF-CHAT-16** | A resposta JSON deve incluir `provider`, `model` (nome no provedor), `model_id` (canônico), `finish_reason` e `provider_usage` (tokens de entrada/saída, quando disponíveis), além do objeto `usage` de cota. | M |
| **RF-CHAT-17** | Requisições de compatibilidade não usam o schema nativo, mas compartilham o mesmo pipeline de validação, cota, roteamento, timeout e logging de metadados. | M |

#### 3.2.3 Limites de uso (RF-USO)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-USO-01** | O guest deve ter uma **cota diária** (`GUEST_DAILY_MESSAGES`). Ao esgotá-la, `POST /chat` retorna `429` com `guest_daily_limit_reached` e `resets_at` (ISO 8601 UTC). | M |
| **RF-USO-02** | O guest deve ter um **limite por minuto** (`GUEST_RATE_PER_MIN`), em janela deslizante de 60 s. Ao excedê-lo, retornar `429` com `Retry-After` e `rate_limited`. | M |
| **RF-USO-03** | O guest deve ter limites de **tamanho de contexto** menores que os do admin. Ao excedê-los, retornar `422` com `errors[]`. | M |
| **RF-USO-04** | Além da cota por `gid`, a API deve aplicar uma **cota diária por IP** (`GUEST_IP_DAILY_MESSAGES`), somando todos os guests do mesmo IP. A cota efetiva é o menor valor restante (RF-USO-12). | M |
| **RF-USO-05** | Uma mensagem é contabilizada **ao iniciar** a geração. Se o provedor falhar antes do primeiro trecho, a contagem deve ser **devolvida** à cota do `gid`/`uid` **e** à do IP. | M |
| **RF-USO-06** | O administrador não possui cota diária, mas mantém um limite técnico por minuto (`RATE_LIMIT_ADMIN_CHAT_PER_MIN`). | M |
| **RF-USO-07** | A API deve oferecer `GET /usage`, que retorna os limites e o uso restante do chamador. Para o admin, os campos diários são `null`. | M |
| **RF-USO-08** | Toda resposta de chat de guest deve incluir `X-RateLimit-Limit`, `X-RateLimit-Remaining` e `X-RateLimit-Reset` (epoch seconds UTC), inclusive no `429`. Para o admin, os cabeçalhos diários são omitidos. | M |
| **RF-USO-09** | A cota diária reinicia à 00:00 UTC (janela fixa). | M |
| **RF-USO-10** | O administrador deve poder **zerar a cota** de um guest (`gid`), de um IP ou de um usuário de teste (`uid`). | M |
| **RF-USO-11** | Uma requisição de chat consome exatamente **1 unidade** de cota, independentemente do número de mensagens. | M |
| **RF-USO-12** | Quando a cota por IP for a mais restritiva, o código é `ip_daily_limit_reached`; quando a do `gid` for a mais restritiva, é `guest_daily_limit_reached`. A verificação deve ser atômica por chave. | M |
| **RF-USO-13** | Usuários de teste devem ter cota diária (`TEST_USER_DEFAULT_DAILY_MESSAGES`, sobrescrevível por usuário) e limite por minuto (`TEST_USER_DEFAULT_RATE_PER_MIN`), sem acesso à cota de guest. | M |
| **RF-USO-14** | As rotas de compatibilidade devem consumir as mesmas cotas e aplicar os mesmos limites de contexto, usando a identidade da API key ou do JWT. | M |
| **RF-USO-15** | Cada API key deve ter um limite por minuto (`COMPAT_API_KEY_RATE_PER_MIN`), independente do limite do usuário representado. | M |
| **RF-USO-16** | Requisições barradas por cota, capacidade ou modelo inválido **não** chegam ao provedor e **não** debitam cota. | M |

#### 3.2.4 Administração (RF-ADM)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-ADM-01** | Todos os endpoints `/admin/*` devem ser acessíveis somente com token de papel `admin`; guest, usuário de teste e API key recebem `403 forbidden`. | M |
| **RF-ADM-02** | A API deve fornecer **estatísticas de uso em memória**, desde a última inicialização: total de requisições, guests distintos, mensagens barradas, erros de provedor e média de duração. | S |
| **RF-ADM-03** | A API deve informar o **status dos provedores de IA** habilitados (nome, disponibilidade e modelo padrão), sem expor chaves. | S |
| **RF-ADM-04** | A API deve permitir ao administrador **zerar a cota** de um guest, IP ou usuário de teste. | M |
| **RF-ADM-05** | A API deve permitir ao administrador **listar os guests ativos em memória** (`gid`, IP, uso diário e última atividade). | M |
| **RF-ADM-06** | A API deve permitir ao administrador **criar, listar, consultar, atualizar, redefinir senha e excluir usuários de teste**, individualmente ou em lote. | M |
| **RF-ADM-07** | A API deve expor `GET /admin/db/status` com caminho, tamanho, modo WAL, versão de migração, latência de leitura/escrita e contagem de usuários, sem expor conteúdo sensível. | S |
| **RF-ADM-08** | A API deve permitir **limpar usuários de teste expirados** (`POST /admin/users/cleanup`), atingindo somente `is_test = 1` e nunca o admin. | M |
| **RF-ADM-09** | Nenhum endpoint administrativo pode excluir, desativar, rebaixar ou alterar a senha do admin; tentativas retornam `409 admin_protected`. | M |
| **RF-ADM-10** | A criação em lote deve respeitar `TEST_USER_MAX_BULK`, retornar as credenciais geradas **uma única vez** e nunca gravá-las em log. | M |

#### 3.2.5 Sistema (RF-SIS)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-SIS-01** | A API deve expor `GET /health` (público) indicando se o serviço está no ar. | M |
| **RF-SIS-02** | A API deve expor documentação interativa OpenAPI automática. | M |
| **RF-SIS-03** | A API deve incluir `X-Request-ID` em todas as respostas, aceitando um UUID válido do cliente ou gerando um novo; o mesmo valor aparece no corpo dos erros e nos logs. | M |
| **RF-SIS-04** | O health check deve poder incluir a disponibilidade dos provedores (`GET /health/ready`). | C |
| **RF-SIS-05** | `GET /health/ready` deve agregar o status de todos os provedores habilitados e do SQLite, retornando `503` quando um componente essencial estiver indisponível. | M |
| **RF-SIS-06** | A API deve expor `GET /health/db` (público ou autenticado, conforme decisão operacional) com `status`, `latency_ms` e `migration_version`, sem detalhes sensíveis. | C |

#### 3.2.6 Gateway multi-provedor (RF-GTW)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-GTW-01** | A API deve manter um **registry de adapters** (`ollama`, `ollama_cloud`, `openai`, `anthropic`, `google`, `openai_compatible`, `fake`), cada um implementando `AIProvider`. | M |
| **RF-GTW-02** | O roteamento deve usar o **Model ID** `provider/model`; nomes sem prefixo resolvem para o provedor padrão (`AI_DEFAULT_MODEL`) ou para um alias. | M |
| **RF-GTW-03** | Na inicialização, cada provedor habilitado deve ter credenciais e base URL válidas; caso contrário, a API **recusa-se a iniciar** com mensagem clara. | M |
| **RF-GTW-04** | `GET /api/v1/models` deve agregar os catálogos dos provedores habilitados, com cache curto e indicação de `selectable`. | M |
| **RF-GTW-05** | O gateway deve aplicar `AI_ALLOWED_MODELS` com suporte a globs e aliases, rejeitando modelos fora da allowlist com `422`. | M |
| **RF-GTW-06** | O gateway deve validar **capacidades** (`stream`, `tools`, `vision`, `json_mode`) antes de chamar o provedor; incompatibilidade retorna `422` (`unsupported_capability`) sem consumir cota. | M |
| **RF-GTW-07** | O fallback (`AI_FALLBACK_CHAIN`) só pode ocorrer **antes do primeiro token**; depois disso, a resposta segue no provedor original. A cota é debitada uma única vez. | S |
| **RF-GTW-08** | O gateway deve normalizar erros e metadados de uso entre provedores, sem vazar chaves, headers ou corpos internos. | M |
| **RF-GTW-09** | O catálogo deve informar `provider`, `model_id`, `name`, `capabilities`, `context_window` (quando disponível) e `selectable`. | M |
| **RF-GTW-10** | Adicionar um novo provedor deve exigir apenas um novo adapter, registro no registry e variáveis de ambiente, sem alterar rotas. | M |

#### 3.2.7 Compatibilidade (RF-CMP)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-CMP-01** | A API deve expor `POST /v1/chat/completions` e `GET /v1/models` compatíveis com a API OpenAI, incluindo streaming SSE com `data: [DONE]`. | M |
| **RF-CMP-02** | A API deve expor `POST /v1/messages` e `POST /v1/messages/count_tokens` compatíveis com a Messages API da Anthropic, incluindo blocos de conteúdo, ferramentas e eventos SSE tipados. | M |
| **RF-CMP-03** | A API deve expor `POST /api/chat`, `GET /api/tags`, `POST /api/show` e `GET /api/ps` compatíveis com a API do Ollama, incluindo streaming NDJSON. | M |
| **RF-CMP-04** | A camada de compatibilidade deve traduzir papéis, `finish_reason`/`stop_reason`, tokens, ferramentas e erros entre o contrato externo e o interno. | M |
| **RF-CMP-05** | As rotas de compatibilidade devem ser autenticadas por API key NEXA, com exceção de `COMPAT_ALLOW_ANONYMOUS=true` (somente desenvolvimento). | M |
| **RF-CMP-06** | As rotas de compatibilidade devem aplicar cotas, rate limit, allowlist e validação de modelo, retornando os erros no envelope da API externa. | M |
| **RF-CMP-07** | O campo `model` das rotas de compatibilidade deve aceitar Model ID, alias ou nome do provedor; a resposta deve refletir o modelo resolvido. | M |
| **RF-CMP-08** | Parâmetros de geração enviados por clientes externos (`temperature`, `top_p`, `max_tokens`, `stop`, etc.) devem ser aceitos, **clampados** aos limites do servidor e documentados; parâmetros desconhecidos podem ser ignorados ou rejeitados conforme a superfície. | S |
| **RF-CMP-09** | Nenhuma rota de compatibilidade pode expor chaves de provedor, headers internos, `request_id` de terceiros ou conteúdo de erro bruto do provedor. | M |
| **RF-CMP-10** | As rotas de compatibilidade não devem persistir conteúdo nem manter estado de conversa; cada requisição é independente. | M |
| **RF-CMP-11** | A compatibilidade com Claude Code deve cobrir `tools`, `tool_use`, `system` em blocos, streaming e `count_tokens`. | M |
| **RF-CMP-12** | As rotas de compatibilidade devem aparecer em OpenAPI separado (`/v1/openapi.json` e `/api/openapi.json`) ou em documentação dedicada, com exemplos. | S |

#### 3.2.8 Usuários e banco (RF-USR)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-USR-01** | O SQLite deve armazenar **somente usuários** (admin e usuários de teste) e metadados de migração; nenhum conteúdo de chat, mensagem ou resposta. | M |
| **RF-USR-02** | Na inicialização, a API deve garantir a existência do **admin** no SQLite a partir de `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`, com ID fixo e `is_protected = 1`, atualizando o espelho sem nunca recriar ou duplicar a linha. | M |
| **RF-USR-03** | O login do admin deve funcionar **mesmo se o SQLite estiver indisponível, vazio ou corrompido**, usando a configuração como fonte de verdade (fallback). | M |
| **RF-USR-04** | O admin não pode ser excluído, desativado, rebaixado ou ter o papel alterado por API, migração ou rotina de limpeza; triggers do SQLite e guardas da aplicação devem impedir a operação. | M |
| **RF-USR-05** | A API deve permitir criar usuários de teste com `username`, `password`, `role = user`, `is_test = 1`, limites opcionais e `expires_at` opcional. | M |
| **RF-USR-06** | A criação em lote deve gerar senhas aleatórias fortes, retorná-las uma única vez e nunca registrá-las em log. | M |
| **RF-USR-07** | `username` deve ser único e normalizado (case-insensitive); `email`, quando informado, também deve ser único. | M |
| **RF-USR-08** | A limpeza automática deve remover apenas usuários com `is_test = 1` e `expires_at` vencido; o admin e usuários não marcados como teste são preservados. | M |
| **RF-USR-09** | As migrações devem ser versionadas, idempotentes e executadas no boot; antes de migrar, a API deve criar backup quando `DB_BACKUP_BEFORE_MIGRATION=true`. | M |
| **RF-USR-10** | Se, após a migração, o admin não existir ou estiver inválido, a API deve **recusar-se a iniciar** e restaurar o backup. | M |
| **RF-USR-11** | As consultas de autenticação devem usar cache em memória com TTL curto e invalidação em escrita, sem servir dados obsoletos após alteração de senha/desativação. | M |
| **RF-USR-12** | O banco deve operar em WAL, com uma conexão de escrita serializada e pool de leitura, sem bloquear o event loop. | M |
| **RF-USR-13** | O endpoint `GET /admin/users` deve suportar paginação por cursor e filtros por papel, `is_test` e `is_active`, sem `SELECT *`. | M |
| **RF-USR-14** | O admin deve poder redefinir a senha de um usuário de teste e desativá-lo; a alteração invalida o cache e os tokens emitidos passam a falhar na próxima verificação. | M |
| **RF-USR-15** | Nenhuma senha, hash ou API key pode aparecer em resposta, log ou mensagem de erro. | M |

---

### 3.3 Requisitos de estado e configuração

| ID | Requisito |
|---|---|
| **RD-01** | O SQLite é o único banco da v2.0 e armazena **somente usuários** e controle de migrações. Não há tabelas de conversas, mensagens, feedback ou auditoria. |
| **RD-02** | A credencial do admin vem de `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH`; o SQLite mantém um espelho para integridade referencial e listagens. A configuração é a fonte de verdade no boot e no fallback. |
| **RD-03** | Os contadores de uso (por `gid`/`uid`, por IP, por minuto e por API key) e de tentativas de login ficam **em memória** (ou em Redis, quando `REDIS_URL` estiver definido) e expiram sozinhos. |
| **RD-04** | O conteúdo das mensagens não é armazenado em nenhuma superfície. Logs e métricas guardam apenas metadados (papel, provedor, modelo, duração, tamanho, status). |
| **RD-05** | O `gid` do guest é um UUID v4 gerado pela API e embutido no token; o servidor não mantém lista de tokens emitidos. |
| **RD-06** | IPs usados nos contadores devem ser obtidos corretamente atrás de proxy reverso: `X-Forwarded-For` só é considerado quando a conexão vem de `TRUSTED_PROXIES`. |
| **RD-07** | Datas são geradas e retornadas em UTC. `resets_at` usa ISO 8601; `X-RateLimit-Reset` usa epoch seconds. |
| **RD-08** | O objeto `usage` tem um único schema e deve ser idêntico em todas as respostas que o exponham (token de convidado, `/usage`, resposta JSON do chat e evento `done`). |
| **RD-09** | O SQLite usa WAL, `synchronous=NORMAL`, `busy_timeout`, cache e mmap configuráveis; o arquivo fica em volume persistente e com permissão restrita. |
| **RD-10** | A linha do admin tem ID fixo (`00000000-0000-0000-0000-000000000001`), `role = admin`, `is_test = 0` e `is_protected = 1`. Triggers impedem `DELETE` e alteração de papel/proteção/atividade. |
| **RD-11** | Usuários de teste têm `is_test = 1`, `role = user` e podem ter `expires_at`; são descartáveis e não participam de nenhuma regra de privilégio. |
| **RD-12** | As migrações são arquivos SQL numerados, aplicados em ordem e registrados em `schema_migrations`; nunca removem usuários. |
| **RD-13** | O cache de autenticação é local ao processo, com TTL (`USERS_CACHE_TTL_SECONDS`) e invalidação explícita em criação, atualização, redefinição de senha e exclusão. |
| **RD-14** | Não há histórico de conversas. O cliente reenvia o contexto completo a cada requisição. |
| **RD-15** | As API keys de compatibilidade vivem apenas em `COMPAT_API_KEYS`; não há tabela de chaves nesta versão. |
| **RD-16** | As credenciais dos provedores vivem apenas em variáveis de ambiente/cofre; nunca são gravadas no SQLite nem retornadas. |
| **RD-17** | O catálogo de modelos é cacheado por `MODELS_CACHE_SECONDS` e pode ser atualizado com `?refresh=true`. |
| **RD-18** | O backup do SQLite é feito com checkpoint WAL + cópia consistente (ou `VACUUM INTO`) antes de migrações e em rotina operacional documentada. |
| **RD-19** | As rotas de compatibilidade são traduções stateless; não criam sessão, conversa ou estado no servidor. |
| **RD-20** | Logs de acesso e de chat incluem superfície (`native`, `openai`, `anthropic`, `ollama`), provedor, modelo e latência, mas nunca conteúdo, tokens ou chaves. |

---

### 3.4 Requisitos não funcionais

#### 3.4.1 Desempenho

| ID | Requisito |
|---|---|
| **RNF-DES-01** | Endpoints sem IA (token de convidado, uso, admin, health) devem responder em até **100 ms** no p95. `/auth/login` tem meta de **300 ms** p95, por incluir a verificação de hash. |
| **RNF-DES-02** | O overhead da API sobre o provedor no início do streaming deve ser inferior a **200 ms**; o tempo até o primeiro token depende do provedor/hardware. |
| **RNF-DES-03** | A API deve ser assíncrona e não bloquear o event loop em chamadas de IA, rede ou banco; operações SQLite síncronas devem rodar em threadpool/`aiosqlite`. |
| **RNF-DES-04** | A API deve suportar no mínimo **50 conexões de streaming simultâneas** em um único nó, limitada pela capacidade do provedor. |
| **RNF-DES-05** | A checagem de limites de uso deve ter custo O(1) por requisição, com estruturas por chave limitadas ao tamanho da janela. |
| **RNF-DES-06** | A leitura de usuário por `username` no SQLite deve ter **p95 ≤ 2 ms** e **p99 ≤ 5 ms** em volume local, com índice único. |
| **RNF-DES-07** | A listagem paginada de **10 mil usuários** deve responder em **p95 ≤ 20 ms**, usando cursor e índice adequado. |
| **RNF-DES-08** | A escrita de usuário (criação/alteração) deve ter **p95 ≤ 10 ms**, com transação curta e escrita serializada. |
| **RNF-DES-09** | A aplicação de migrações no boot deve levar **≤ 200 ms** para o schema da v2.0 e não pode bloquear o event loop por mais de 1 s. |
| **RNF-DES-10** | A tradução de uma requisição de compatibilidade (parse + mapeamento + serialização) deve adicionar **≤ 20 ms** p95 sobre o pipeline nativo. |
| **RNF-DES-11** | O cache de autenticação deve responder em **≤ 0,5 ms** p95 e ter teto de entradas (`USERS_CACHE_MAX_ENTRIES`). |
| **RNF-DES-12** | O SQLite deve usar WAL, mmap, cache de páginas e `PRAGMA optimize`; consultas devem ser cobertas por índices e verificadas com `EXPLAIN QUERY PLAN` nos testes. |

#### 3.4.2 Segurança

| ID | Requisito |
|---|---|
| **RNF-SEG-01** | Hash de senha com **argon2id** (preferencial) ou bcrypt, com salt. |
| **RNF-SEG-02** | Access token com validade curta e configurável (`ADMIN_TOKEN_EXPIRE_MINUTES`, `GUEST_TOKEN_EXPIRE_HOURS`). |
| **RNF-SEG-03** | Em produção, todo o tráfego deve ocorrer sobre HTTPS/TLS. |
| **RNF-SEG-04** | CORS restrito às origens configuradas, com preflight e `Access-Control-Expose-Headers` cobrindo `X-RateLimit-*`, `Retry-After`, `X-Request-ID` e `Content-Type`. |
| **RNF-SEG-05** | Proteção contra força bruta no login: limite por IP, bloqueio temporário e `429 login_locked` com `Retry-After`. |
| **RNF-SEG-06** | Validação rigorosa de entrada com Pydantic; rejeitar campos desconhecidos nos corpos sensíveis (`extra="forbid"`), exceto nas rotas de compatibilidade, que devem aceitar o contrato externo e ignorar/rejeitar parâmetros documentados. |
| **RNF-SEG-07** | Segredos (`JWT_SECRET`, `ADMIN_PASSWORD_HASH`, chaves de provedores e `COMPAT_API_KEYS`) somente em variáveis de ambiente ou cofre; nunca no repositório. |
| **RNF-SEG-08** | Chaves de provedores e API keys NEXA nunca devem ser expostas ao cliente nem aparecer em logs, respostas ou traces. |
| **RNF-SEG-09** | Limite de tamanho do corpo (`MAX_REQUEST_BODY_BYTES`, padrão 2 MB), compatível com o maior payload permitido. |
| **RNF-SEG-10** | Cabeçalhos de segurança básicos (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; HSTS em produção). |
| **RNF-SEG-11** | O conteúdo gerado pela IA é dado não confiável; a API não o executa nem o interpreta como instrução. |
| **RNF-SEG-12** | `JWT_SECRET` deve ter no mínimo 32 bytes de entropia; em produção, a API recusa segredo fraco/exemplo e hash ausente/inválido. |
| **RNF-SEG-13** | Guest e usuário de teste não acessam nenhum recurso privilegiado; a defesa contra abuso é a cota por IP + limite de emissão de tokens. |
| **RNF-SEG-14** | API keys NEXA devem ser comparadas em tempo constante (`hmac.compare_digest`) e ter prefixo/fingerprint para auditoria. |
| **RNF-SEG-15** | O arquivo SQLite deve ter permissão `0600` (ou equivalente) e o diretório `0700`; backups herdam a mesma restrição. |
| **RNF-SEG-16** | Todas as consultas SQL devem ser parametrizadas; proibida concatenação de entrada do usuário em SQL. |
| **RNF-SEG-17** | O admin é imutável por API e por trigger; qualquer tentativa gera log de segurança e `409 admin_protected`. |
| **RNF-SEG-18** | Migrações devem ser revisadas e executadas com backup; a API recusa iniciar se o admin não sobreviver à migração. |

#### 3.4.3 Confiabilidade e disponibilidade

| ID | Requisito |
|---|---|
| **RNF-CON-01** | Timeout configurável nas chamadas ao provedor (`AI_TIMEOUT_SECONDS`); no streaming, detecção de inatividade (`AI_STREAM_IDLE_TIMEOUT_SECONDS`) e limites de saída (`AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS`). |
| **RNF-CON-02** | Falhas de provedor não devem derrubar a API nem afetar endpoints que não dependem dele. |
| **RNF-CON-03** | Disponibilidade-alvo de **99%** mensal para a API (excluindo indisponibilidade de provedores). |
| **RNF-CON-04** | Encerramento gracioso, permitindo concluir requisições e streams dentro de `SHUTDOWN_TIMEOUT_SECONDS`. |
| **RNF-CON-05** | A API deve iniciar sem dependência externa além dos provedores habilitados (e Redis, se configurado). O SQLite é local. |
| **RNF-CON-06** | Com contadores em memória, a API deve operar com **um único processo**; recusa `UVICORN_WORKERS > 1` sem `REDIS_URL`. |
| **RNF-CON-07** | Falha do SQLite não pode impedir o login do admin (fallback de configuração); usuários de teste recebem `503 database_unavailable`. |
| **RNF-CON-08** | O backup e a restauração do SQLite devem ser documentados e testados; a restauração nunca pode remover o admin. |

#### 3.4.4 Manutenibilidade e extensibilidade

| ID | Requisito |
|---|---|
| **RNF-MAN-01** | Padrão **Strategy/Adapter** para provedores: `AIProvider` com `generate`, `stream`, `health`, `list_models` e `capabilities`; seleção via registry/factory. |
| **RNF-MAN-02** | Adicionar um provedor deve exigir apenas: novo adapter, registro no registry e variáveis de ambiente. |
| **RNF-MAN-03** | Estrutura modular: `api/` (rotas), `compat/` (traduções), `core/` (segurança, cotas, usuários), `db/` (SQLite/migrações), `ai/` (provedores), `schemas/`. |
| **RNF-MAN-04** | O armazenamento de contadores deve ficar atrás de `UsageStore` (memória/Redis). |
| **RNF-MAN-05** | A autenticação deve ficar atrás de `AuthService`/`UserRepository`, permitindo trocar a fonte de usuários sem alterar rotas. |
| **RNF-MAN-06** | Cobertura mínima de **80%** nas camadas de serviço e rotas. |
| **RNF-MAN-07** | Código aderente à PEP 8, com Ruff, mypy e CI. |
| **RNF-MAN-08** | Dependências fixadas em `requirements.txt` e lock. |
| **RNF-MAN-09** | As rotas de compatibilidade devem ter **testes de contrato** por superfície, com fixtures de request/response reais. |
| **RNF-MAN-10** | Migrações SQL devem ser versionadas, revisadas e cobertas por teste de upgrade a partir de um banco vazio e de um banco v1.1.0. |

#### 3.4.5 Portabilidade

| ID | Requisito |
|---|---|
| **RNF-POR-01** | Execução em contêiner com `Dockerfile` e `docker-compose` (API + Ollama opcional + volume para o SQLite). |
| **RNF-POR-02** | Toda configuração vem de variáveis de ambiente (12-factor). |
| **RNF-POR-03** | A imagem declara `HEALTHCHECK` em `/health` e executa um único processo enquanto os contadores forem em memória. |
| **RNF-POR-04** | O volume do SQLite deve ser persistente, local e com backup; volumes de rede não são suportados oficialmente. |

#### 3.4.6 Observabilidade

| ID | Requisito |
|---|---|
| **RNF-OBS-01** | Logs estruturados (JSON) com nível, data/hora, `request_id`, rota, status e latência. |
| **RNF-OBS-02** | Logs não devem conter senhas, tokens, API keys, chaves de provedor nem conteúdo de mensagens, em nenhum nível. |
| **RNF-OBS-03** | Métricas por requisição de chat: papel/superfície, provedor, modelo, duração e tokens quando disponíveis. |
| **RNF-OBS-04** | Eventos de segurança (falha de login, lockout, limite atingido, tentativa de alterar o admin, chave inválida) devem ser registrados. |
| **RNF-OBS-05** | O `X-Request-ID` deve aparecer em todas as linhas de log da requisição e ser propagado ao provedor quando o protocolo permitir. |
| **RNF-OBS-06** | Medir latência do SQLite (leitura/escrita) e expor no `/admin/db/status`; alertar quando p95 exceder as metas. |
| **RNF-OBS-07** | Logs de compatibilidade devem identificar a superfície e o modelo resolvido, sem registrar o corpo traduzido. |

#### 3.4.7 Usabilidade da API

| ID | Requisito |
|---|---|
| **RNF-USA-01** | Respostas de erro padronizadas e legíveis, com envelope correto por superfície. |
| **RNF-USA-02** | Nomes de recursos e campos consistentes, em inglês e `snake_case` na API nativa. |
| **RNF-USA-03** | Documentação OpenAPI com descrição e exemplos para todos os endpoints nativos. |
| **RNF-USA-04** | Erros de limite devem trazer motivo, `Retry-After` ou `resets_at`. |
| **RNF-USA-05** | Nomes de campos, rotas e códigos em inglês; mensagens de `title`/`detail` em pt-BR na API nativa. |
| **RNF-USA-06** | O chat nativo com `stream = true` e `stream = false` deve ser documentado no OpenAPI com os dois tipos de resposta. |
| **RNF-USA-07** | Cada superfície de compatibilidade deve ter documentação dedicada com exemplos `curl` e a lista de campos suportados/ignorados. |
| **RNF-USA-08** | A mensagem de erro de modelo deve indicar o Model ID esperado (`provider/model`) e os provedores disponíveis. |

---

### 3.5 Regras de negócio

| ID | Regra |
|---|---|
| **RN-01** | Existe **um único usuário permanente**: o administrador, definido na configuração e espelhado no SQLite. Não há cadastro público. |
| **RN-02** | Qualquer visitante pode usar o chat como **guest**, sem cadastro, dentro dos limites de uso. |
| **RN-03** | O administrador não tem cota diária, mas está sujeito ao limite técnico por minuto e aos limites de tamanho do seu papel. |
| **RN-04** | Alterar o administrador (usuário ou senha) exige alterar a configuração e reiniciar a API. Tokens já emitidos continuam válidos até expirar; para invalidá-los de imediato, troca-se o `JWT_SECRET`. |
| **RN-05** | O chat é efêmero: a API não guarda histórico. O cliente mantém e reenvia o contexto. |
| **RN-06** | Mensagens `system` do cliente são aceitas somente se `ALLOW_CLIENT_SYSTEM_MESSAGES = true` (e nunca para guest); caso contrário, são rejeitadas com `422`. Se aceitas, devem vir antes da primeira mensagem `user`; o `DEFAULT_SYSTEM_PROMPT`, se definido, é prefixado. Não existe `system_prompt` separado. |
| **RN-07** | O modelo não possui memória; o contexto enviado é encaminhado integralmente ao provedor após validação e limites. |
| **RN-08** | A cota do guest é medida por `gid` **e** por IP; a cota efetiva é o menor valor restante. Novo token renova o `gid`, mas não o IP. |
| **RN-09** | A cota diária reinicia à 00:00 UTC (janela fixa); `resets_at` é ISO 8601 UTC e `X-RateLimit-Reset` é epoch seconds. O limite por minuto usa janela deslizante de 60 s. |
| **RN-10** | Mensagem barrada por limite não consome cota nem chega ao provedor. Falha do provedor antes do primeiro trecho devolve a cota do `gid`/`uid` e do IP. |
| **RN-11** | Guest e usuário de teste não acessam nenhum endpoint `/admin/*`. |
| **RN-12** | Os contadores de uso são voláteis: reiniciar a API (ou o Redis) zera os limites. |
| **RN-13** | Guest, usuário de teste, admin e sistemas externos podem escolher o modelo, respeitando `AI_ALLOWED_MODELS`; o prompt de sistema e os parâmetros de geração continuam definidos pelo servidor na API nativa. |
| **RN-14** | Uma requisição de chat consome 1 unidade de cota, independentemente do número de mensagens. |
| **RN-15** | Quando as duas cotas forem restritivas, o código prioriza `guest_daily_limit_reached`; se apenas a do IP estiver esgotada, `ip_daily_limit_reached`. |
| **RN-16** | "Guest" é o termo canônico em código (`role`, `GUEST_*`); "ghost" e "visitante" são sinônimos de documentação/interface. |
| **RN-17** | O admin é **permanente**; usuários de teste são **descartáveis**. Limpeza, migração ou reset nunca podem remover o admin. |
| **RN-18** | O admin não pode ser excluído, desativado, rebaixado ou ter o papel alterado; a proteção existe na aplicação e no SQLite (triggers). |
| **RN-19** | O login do admin usa a configuração como fonte de verdade e funciona sem o SQLite; o banco é um espelho operacional. |
| **RN-20** | A limpeza automática atinge somente `is_test = 1` com `expires_at` vencido; nunca `is_protected = 1`. |
| **RN-21** | O Model ID canônico é `provider/model`; nomes sem prefixo resolvem para o provedor padrão ou para um alias declarado. |
| **RN-22** | O modelo padrão do servidor é sempre permitido, mesmo fora da allowlist; os demais exigem `AI_ALLOWED_MODELS`. |
| **RN-23** | O fallback só ocorre antes do primeiro token; depois, a resposta continua no provedor original. A cota é debitada uma única vez. |
| **RN-24** | Incompatibilidade de capacidade (ex.: `tools` em modelo sem suporte) retorna `422 unsupported_capability` sem consumir cota. |
| **RN-25** | As rotas de compatibilidade compartilham cotas, rate limit, allowlist e timeouts do núcleo; não têm cota própria, exceto o limite por API key. |
| **RN-26** | Erros nas rotas de compatibilidade usam o envelope da API externa (OpenAI, Anthropic ou Ollama), não o Problem Details nativo. |
| **RN-27** | API keys NEXA são segredos de operação; só o prefixo/fingerprint aparece em log. |
| **RN-28** | Chaves de provedores nunca são expostas, nem em erros de configuração; a mensagem indica a variável ausente, não o valor. |
| **RN-29** | Nenhuma superfície persiste conversa; o SQLite contém apenas usuários e migrações. |
| **RN-30** | O SQLite tem um único escritor; escritas são serializadas e curtas, e leituras usam pool/WAL para não bloquear. |

---

### 3.6 Restrições de projeto

| ID | Restrição |
|---|---|
| **RP-01** | Linguagem Python; framework FastAPI. |
| **RP-02** | **SQLite é o único banco** e armazena somente usuários/migrações. Proibidas tabelas de conversa, mensagem, feedback ou auditoria persistente. |
| **RP-03** | Autenticação baseada em JWT (`PyJWT`), com suporte nativo OAuth2/Bearer do FastAPI. |
| **RP-04** | Comunicação com provedores via `httpx` assíncrono. |
| **RP-05** | Streaming nativo via SSE com `StreamingResponse`, cabeçalhos anti-buffer e heartbeat. |
| **RP-06** | Padrão Adapter/Strategy obrigatório; a troca de provedor ocorre por configuração/Model ID, sem alterar rotas. |
| **RP-07** | Contadores em memória (um processo) ou Redis (vários nós/processos). Com mais de um processo sem Redis, a API recusa iniciar. |
| **RP-08** | Nenhum e-mail é enviado nesta versão. |
| **RP-09** | O frontend consome o SSE nativo via `fetch`/`ReadableStream`; `EventSource` não é compatível com `POST` + `Authorization`. |
| **RP-10** | SQLite acessado com SQL parametrizado (`aiosqlite` ou `sqlite3` em threadpool), sem ORM pesado; migrações em SQL versionado. |
| **RP-11** | As rotas de compatibilidade são camadas de tradução; não duplicam regra de negócio nem acessam o banco diretamente. |
| **RP-12** | API keys NEXA vivem em `COMPAT_API_KEYS`; não há gestão de chaves no banco nesta versão. |
| **RP-13** | A proteção do admin é implementada em duas camadas: guarda na aplicação + triggers no SQLite. |
| **RP-14** | WAL obrigatório em produção; `synchronous=NORMAL` é o padrão, com `FULL` disponível para requisitos mais rígidos. |
| **RP-15** | Escritas no SQLite são serializadas por uma única conexão/lock assíncrono; leituras usam pool. |
| **RP-16** | Nenhum conteúdo de chat pode ser gravado em disco, banco, cache ou log. |
| **RP-17** | Adapters de provedor são stateless e não guardam credenciais em memória além do necessário para a chamada. |
| **RP-18** | Os prefixos de compatibilidade são fixos (`/v1` e `/api`) para preservar a compatibilidade com clientes existentes. |

---

### 3.7 Catálogo de endpoints

Prefixos: nativo `/api/v1`; compatibilidade OpenAI `/v1`; Anthropic `/v1`; Ollama `/api`.
**Auth:** 🔓 público · 🔐 guest/usuário/admin · 🛡️ somente admin · 🔑 API key NEXA (ou JWT).

#### 3.7.1 Autenticação (nativa)

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| POST | `/api/v1/auth/login` | 🔓 | Login do admin ou de usuário de teste; retorna JWT | RF-AUT-02 a 04, 06, 12 |
| POST | `/api/v1/auth/guest` | 🔓 | Emite token de convidado | RF-AUT-05, 06, 08 |

**Corpo do login**

```json
{ "username": "admin", "password": "********" }
```

**Resposta do login (`200 OK`)**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600,
  "role": "admin",
  "user_id": "00000000-0000-0000-0000-000000000001"
}
```

Para usuário de teste, `role` é `user` e `user_id` é o UUID do usuário.

**Resposta do token de convidado (`200 OK`)**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 86400,
  "role": "guest",
  "usage": {
    "daily_limit": 10,
    "daily_remaining": 10,
    "per_minute_limit": 3,
    "per_minute_remaining": 3,
    "resets_at": "2026-10-30T00:00:00Z"
  }
}
```

O `usage` na emissão refere-se ao `gid` recém-criado; quando a cota do IP já for mais restritiva, `daily_remaining` reflete a cota efetiva. Excesso de emissão por IP: `429 guest_token_limit_reached` com `Retry-After`.

#### 3.7.2 Uso

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/api/v1/usage` | 🔐 | Limites e uso restante do chamador | RF-USO-07 |

**Resposta (`200 OK`, guest)**

```json
{
  "role": "guest",
  "daily_limit": 10,
  "daily_remaining": 7,
  "per_minute_limit": 3,
  "per_minute_remaining": 3,
  "resets_at": "2026-10-30T00:00:00Z"
}
```

**Resposta (`200 OK`, admin)** — sem cota diária:

```json
{
  "role": "admin",
  "daily_limit": null,
  "daily_remaining": null,
  "per_minute_limit": 60,
  "per_minute_remaining": 59,
  "resets_at": null
}
```

#### 3.7.3 Chat nativo

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| POST | `/api/v1/chat` | 🔐 | Envia mensagens e recebe a resposta (JSON ou SSE) | RF-CHAT-01 a 17, RF-USO-01 a 16 |

**Corpo da requisição**

```json
{
  "messages": [
    {"role": "user", "content": "Olá!"},
    {"role": "assistant", "content": "Olá! Como posso ajudar?"},
    {"role": "user", "content": "Explique o que é SSE."}
  ],
  "stream": true,
  "model": "ollama/llama3.1",
  "tools": [],
  "tool_choice": "auto"
}
```

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `messages` | array | Sim | Lista completa do contexto; a última deve ser `role = user` |
| `stream` | booleano | Não | `true` (padrão): SSE; `false`: JSON completo |
| `model` | string | Não | Model ID `provider/model` ou alias; omitido, usa `AI_DEFAULT_MODEL` |
| `tools` | array | Não | Ferramentas no formato interno; requer capacidade `tools` |
| `tool_choice` | string/objeto | Não | `auto`, `none`, `required` ou ferramenta específica |

Campos desconhecidos são rejeitados com `422` (`extra="forbid"`). Não há `system_prompt` nem parâmetros de geração no corpo nativo (RF-CHAT-09). A validação do modelo e da capacidade ocorre **antes** do consumo de cota.

**Resposta com `stream = false` (`200 OK`)**

```json
{
  "message": {
    "role": "assistant",
    "content": "SSE é ...",
    "tool_calls": []
  },
  "provider": "ollama",
  "model": "llama3.1:latest",
  "model_id": "ollama/llama3.1",
  "finish_reason": "stop",
  "provider_usage": {"prompt_tokens": 42, "completion_tokens": 128},
  "usage": {
    "daily_limit": 10,
    "daily_remaining": 6,
    "per_minute_limit": 3,
    "per_minute_remaining": 2,
    "resets_at": "2026-10-30T00:00:00Z"
  }
}
```

Para o admin, `daily_limit`, `daily_remaining` e `resets_at` são `null`. `finish_reason` ∈ {`stop`, `length`, `timeout`, `tool_use`}. `model` é o nome no provedor; `model_id` é o canônico resolvido.

**Resposta com `stream = true` (`200 OK`, `text/event-stream`)**

Cabeçalhos obrigatórios: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no` e os `X-RateLimit-*`. Heartbeat `: ping` a cada 15 s.

```
: ping

data: {"content": "SSE é "}

data: {"content": "um mecanismo ..."}

event: done
data: {"status": "complete", "finish_reason": "stop", "model_id": "ollama/llama3.1", "provider_usage": {"prompt_tokens": 42, "completion_tokens": 128}, "usage": {"daily_limit": 10, "daily_remaining": 6, "per_minute_limit": 3, "per_minute_remaining": 2, "resets_at": "2026-10-30T00:00:00Z"}}

```

Em caso de falha após o início:

```
event: error
data: {"code": "ai_provider_error", "detail": "O provedor de IA não respondeu.", "request_id": "b1f4c7e2-..."}

```

**Resposta ao exceder o limite (`429 Too Many Requests`)**

```json
{
  "type": "about:blank",
  "title": "Limite de uso atingido",
  "status": 429,
  "code": "guest_daily_limit_reached",
  "detail": "Você atingiu o limite diário de mensagens do modo visitante.",
  "resets_at": "2026-10-30T00:00:00Z",
  "request_id": "b1f4c7e2-..."
}
```

#### 3.7.4 Administração

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/api/v1/admin/stats` | 🛡️ | Estatísticas em memória | RF-ADM-02 |
| GET | `/api/v1/admin/ai/status` | 🛡️ | Status dos provedores | RF-ADM-03 |
| GET | `/api/v1/admin/guests` | 🛡️ | Guests ativos e uso por IP | RF-ADM-05 |
| POST | `/api/v1/admin/usage/reset` | 🛡️ | Zera cota de `gid`, `ip` ou `uid` | RF-ADM-04, RF-USO-10 |
| GET | `/api/v1/admin/db/status` | 🛡️ | Status e latência do SQLite | RF-ADM-07 |
| POST | `/api/v1/admin/users` | 🛡️ | Cria um usuário de teste | RF-ADM-06, RF-USR-05 |
| POST | `/api/v1/admin/users/bulk` | 🛡️ | Cria usuários de teste em lote | RF-ADM-06/10, RF-USR-06 |
| GET | `/api/v1/admin/users` | 🛡️ | Lista usuários com paginação/filtros | RF-ADM-06, RF-USR-13 |
| GET | `/api/v1/admin/users/{id}` | 🛡️ | Consulta um usuário | RF-ADM-06 |
| PATCH | `/api/v1/admin/users/{id}` | 🛡️ | Atualiza dados/limites/ativo | RF-ADM-06, RF-USR-14 |
| POST | `/api/v1/admin/users/{id}/reset-password` | 🛡️ | Redefine a senha | RF-ADM-06, RF-USR-14 |
| DELETE | `/api/v1/admin/users/{id}` | 🛡️ | Exclui usuário de teste | RF-ADM-06/09 |
| POST | `/api/v1/admin/users/cleanup` | 🛡️ | Remove usuários de teste expirados | RF-ADM-08 |

**`GET /admin/stats` (`200 OK`)**

```json
{
  "since": "2026-10-29T10:00:00Z",
  "chat_requests_total": 128,
  "guest_distinct": 14,
  "rate_limited_total": 7,
  "provider_errors_total": 2,
  "avg_response_seconds": 3.42
}
```

**`GET /admin/ai/status` (`200 OK`)**

```json
{
  "providers": [
    {"provider": "ollama", "default_model": "llama3.1:latest", "available": true, "checked_at": "2026-10-29T14:30:00Z"},
    {"provider": "openai", "default_model": "gpt-4o-mini", "available": true, "checked_at": "2026-10-29T14:30:00Z"}
  ]
}
```

**`GET /admin/db/status` (`200 OK`)**

```json
{
  "status": "ok",
  "path": "./data/nexa.db",
  "size_bytes": 286720,
  "wal": true,
  "migration_version": 3,
  "users_total": 42,
  "test_users_total": 41,
  "read_latency_ms_p95": 0.8,
  "write_latency_ms_p95": 2.1
}
```

**`POST /admin/users` (`201 Created`)**

```json
{
  "id": "0192f3a4-...",
  "username": "teste01",
  "role": "user",
  "is_test": true,
  "is_active": true,
  "daily_message_limit": 100,
  "rate_per_min": 10,
  "expires_at": "2026-11-05T14:30:00Z",
  "created_at": "2026-10-29T14:30:00Z",
  "password": "Nxa-7f3c9a2e..."
}
```

O campo `password` aparece **somente** na criação/redefinição, com `Cache-Control: no-store`, e nunca é registrado em log.

#### 3.7.5 Sistema

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/health` | 🔓 | Saúde da API | RF-SIS-01 |
| GET | `/health/ready` | 🔓 | Prontidão (provedores + SQLite) | RF-SIS-04/05 |
| GET | `/health/db` | 🔓 | Saúde do SQLite | RF-SIS-06 |
| GET | `/docs`, `/redoc` | 🔓 | Documentação OpenAPI | RF-SIS-02 |

**`GET /health` (`200 OK`)**

```json
{ "status": "ok", "version": "2.0.0", "request_id": "b1f4c7e2-..." }
```

**`GET /health/ready`** — `200` quando os componentes essenciais respondem; `503` com `{"status": "unavailable", "providers": [...], "database": "ok"}` quando algum falha.

#### 3.7.6 Modelos

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/api/v1/models` | 🔐 | Catálogo agregado dos provedores | RF-CHAT-13, RF-GTW-04/09 |

**Parâmetro de query:** `refresh` (booleano) ignora o cache curto.

**Resposta (`200 OK`)**

```json
{
  "default_model": "ollama/llama3.1",
  "providers": [
    {
      "provider": "ollama",
      "name": "Ollama local",
      "available": true,
      "default_model": "llama3.1:latest",
      "models": [
        {
          "model_id": "ollama/llama3.1",
          "name": "llama3.1:latest",
          "size_bytes": 4920739055,
          "modified_at": "2026-10-29T14:30:00Z",
          "family": "llama",
          "parameter_size": "8.0B",
          "quantization_level": "Q4_K_M",
          "capabilities": ["chat", "stream", "tools"],
          "context_window": 131072,
          "selectable": true
        }
      ]
    },
    {
      "provider": "anthropic",
      "name": "Anthropic",
      "available": true,
      "default_model": "claude-sonnet-4-5",
      "models": [
        {
          "model_id": "anthropic/claude-sonnet-4-5",
          "name": "claude-sonnet-4-5",
          "size_bytes": null,
          "modified_at": null,
          "family": "claude",
          "parameter_size": null,
          "quantization_level": null,
          "capabilities": ["chat", "stream", "tools", "vision"],
          "context_window": 200000,
          "selectable": true
        }
      ]
    }
  ],
  "models": [
    {"model_id": "ollama/llama3.1", "provider": "ollama", "name": "llama3.1:latest", "selectable": true},
    {"model_id": "anthropic/claude-sonnet-4-5", "provider": "anthropic", "name": "claude-sonnet-4-5", "selectable": true}
  ]
}
```

O array `models` é um índice plano para clientes simples; `providers` traz o catálogo completo. Consultar o catálogo **não** consome cota. Falhas de um provedor são refletidas em `available=false` sem derrubar a resposta; falha total retorna `503 provider_unavailable`.

#### 3.7.7 Compatibilidade OpenAI

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| POST | `/v1/chat/completions` | 🔑 | Chat no contrato OpenAI (JSON ou SSE) |
| GET | `/v1/models` | 🔑 | Lista de modelos no contrato OpenAI |

**Requisição (`POST /v1/chat/completions`)**

```json
{
  "model": "openai/gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "Seja breve."},
    {"role": "user", "content": "Olá!"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 256,
  "tools": []
}
```

**Resposta (`200 OK`)**

```json
{
  "id": "chatcmpl-nexa-...",
  "object": "chat.completion",
  "created": 1761748200,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Olá! Como posso ajudar?"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
}
```

**Streaming:** cada chunk tem `object: "chat.completion.chunk"` e o fluxo termina com `data: [DONE]`. **Erros:** `{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": "..."}}`.

**`GET /v1/models` (`200 OK`)**

```json
{
  "object": "list",
  "data": [
    {"id": "openai/gpt-4o-mini", "object": "model", "created": 1761748200, "owned_by": "nexa"},
    {"id": "anthropic/claude-sonnet-4-5", "object": "model", "created": 1761748200, "owned_by": "nexa"}
  ]
}
```

#### 3.7.8 Compatibilidade Anthropic (Claude Code)

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| POST | `/v1/messages` | 🔑 | Messages API (JSON ou SSE) |
| POST | `/v1/messages/count_tokens` | 🔑 | Contagem aproximada de tokens de entrada |

**Headers:** `x-api-key: nxa_...` (ou `Authorization: Bearer`), `anthropic-version: 2023-06-01`, `content-type: application/json`.

**Requisição**

```json
{
  "model": "anthropic/claude-sonnet-4-5",
  "max_tokens": 1024,
  "system": "Você é um assistente útil.",
  "messages": [
    {"role": "user", "content": "Liste os arquivos do projeto."}
  ],
  "tools": [
    {
      "name": "list_files",
      "description": "Lista arquivos de um diretório",
      "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    }
  ],
  "stream": true
}
```

**Resposta (`200 OK`)**

```json
{
  "id": "msg_nexa_...",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-5",
  "content": [
    {"type": "text", "text": "Vou listar os arquivos."},
    {"type": "tool_use", "id": "toolu_...", "name": "list_files", "input": {"path": "."}}
  ],
  "stop_reason": "tool_use",
  "stop_sequence": null,
  "usage": {"input_tokens": 42, "output_tokens": 31}
}
```

**Streaming:** `message_start`, `content_block_start`, `content_block_delta` (`text_delta`/`input_json_delta`), `content_block_stop`, `message_delta`, `message_stop`, além de `ping` e `error`. **Erros:** `{"type": "error", "error": {"type": "invalid_request_error", "message": "..."}}`.

**`count_tokens`:** retorna `{"input_tokens": 42}`; usa a contagem do provedor quando disponível ou uma estimativa documentada.

#### 3.7.9 Compatibilidade Ollama

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| POST | `/api/chat` | 🔑 | Chat no contrato Ollama (NDJSON) |
| GET | `/api/tags` | 🔑 | Modelos disponíveis |
| POST | `/api/show` | 🔑 | Detalhes de um modelo |
| GET | `/api/ps` | 🔑 | Modelos em execução (agregado/opcional) |

**Requisição (`POST /api/chat`)**

```json
{
  "model": "ollama/llama3.1",
  "messages": [{"role": "user", "content": "Olá!"}],
  "stream": true,
  "options": {"temperature": 0.7}
}
```

**Resposta não-streaming (`200 OK`)**

```json
{
  "model": "llama3.1:latest",
  "created_at": "2026-10-29T14:30:00Z",
  "message": {"role": "assistant", "content": "Olá! Como posso ajudar?"},
  "done": true,
  "done_reason": "stop",
  "prompt_eval_count": 12,
  "eval_count": 8
}
```

**Streaming:** uma linha JSON por trecho, com `done: false` e a última com `done: true` e `done_reason`. **Erros:** `{"error": "..."}` com o status HTTP correspondente.

#### 3.7.10 Usuários (admin)

As rotas de usuários estão listadas em 3.7.4. Regras adicionais:

- `DELETE /admin/users/{id}` retorna `409 admin_protected` para o admin e `204` para usuário de teste.
- `PATCH` não permite alterar `role`, `is_test`, `is_protected` nem o `username` do admin.
- A listagem usa cursor opaco (`next_cursor`) e limite máximo de 100.
- Nenhuma resposta inclui `password_hash`.

---

### 3.8 Padrão de erros

A API nativa segue uma estrutura inspirada na RFC 9457 (Problem Details):

```json
{
  "type": "about:blank",
  "title": "Não autorizado",
  "status": 401,
  "code": "invalid_credentials",
  "detail": "Usuário ou senha inválidos.",
  "request_id": "b1f4c7e2-..."
}
```

Para erros de validação (`422`), incluir `errors`:

```json
{
  "type": "about:blank",
  "title": "Requisição inválida",
  "status": 422,
  "code": "validation_error",
  "detail": "Um ou mais campos são inválidos.",
  "errors": [
    {"field": "messages.2.role", "reason": "deve ser system, user, assistant ou tool"}
  ],
  "request_id": "b1f4c7e2-..."
}
```

Respostas `401` incluem `WWW-Authenticate: Bearer`. Erros após o início do SSE vão no evento `error`, sem trocar o status HTTP.

**Envelopes de compatibilidade**

| Superfície | Formato |
|---|---|
| OpenAI | `{"error": {"message": "...", "type": "...", "param": null, "code": "..."}}` |
| Anthropic | `{"type": "error", "error": {"type": "...", "message": "..."}}` |
| Ollama | `{"error": "..."}` |

**Tabela de códigos nativos**

| HTTP | Quando ocorre | Exemplo de `code` |
|---|---|---|
| 400 | Requisição malformada | `bad_request` |
| 401 | Token/API key ausente, inválido ou expirado | `invalid_credentials`, `token_expired`, `invalid_token`, `invalid_api_key` |
| 403 | Sem permissão | `forbidden` |
| 404 | Recurso inexistente | `not_found` |
| 409 | Conflito (admin protegido, username duplicado) | `admin_protected`, `username_conflict` |
| 413 | Corpo acima do limite | `payload_too_large` |
| 422 | Validação, modelo, allowlist ou capacidade | `validation_error`, `model_not_found`, `model_not_allowed`, `unsupported_capability` |
| 429 | Cota, rate limit, emissão de token ou login | `rate_limited`, `guest_daily_limit_reached`, `ip_daily_limit_reached`, `guest_token_limit_reached`, `login_locked` |
| 500 | Erro interno | `internal_error` |
| 502 | Erro retornado pelo provedor | `ai_provider_error` |
| 503 | Provedor ou banco indisponível | `provider_unavailable`, `database_unavailable` |
| 504 | Timeout do provedor | `ai_provider_timeout` |

---

## 4. Critérios de aceitação

A API será considerada aceita quando:

| ID | Critério |
|---|---|
| **CA-01** | A API inicia com `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` válidos e **não inicia** se qualquer um estiver ausente. |
| **CA-02** | O admin faz login com credenciais corretas e recebe token; com senha incorreta recebe `401` genérico. |
| **CA-03** | Um visitante obtém token em `POST /auth/guest` e usa o chat, sem cadastro. |
| **CA-04** | `POST /chat` com `stream = true` entrega a resposta incrementalmente e finaliza com `done`. |
| **CA-05** | `POST /chat` com `stream = false` retorna a resposta completa em JSON. |
| **CA-06** | Nenhuma mensagem é persistida: o SQLite contém apenas usuários/migrações e o conteúdo não aparece em log em nenhum nível. |
| **CA-07** | Ao esgotar a cota diária, o guest recebe `429 guest_daily_limit_reached` com `resets_at`, sem chamar o provedor. |
| **CA-08** | Ao exceder o limite por minuto, o guest recebe `429` com `Retry-After`. |
| **CA-09** | Novo token cria novo `gid`, mas a cota por IP continua valendo; a cota efetiva é o menor valor. |
| **CA-10** | A emissão de tokens de convidado é limitada por IP e retorna `429 guest_token_limit_reached` com `Retry-After`. |
| **CA-11** | `GET /usage` mostra o uso restante coerente com os cabeçalhos `X-RateLimit-*`. |
| **CA-12** | O admin envia mensagens sem cota diária; guest/usuário de teste recebe `403` em qualquer `/admin/*`. |
| **CA-13** | Falha do provedor antes do primeiro trecho devolve a cota do `gid`/`uid` **e** do IP. |
| **CA-14** | Com um provedor parado, `POST /chat` retorna `502`/`504` (ou evento `error`) e os demais endpoints continuam funcionando. |
| **CA-15** | Trocar o provedor por configuração/Model ID não exige alteração nas rotas, validado com o provedor fake. |
| **CA-16** | O login é bloqueado temporariamente após `LOGIN_MAX_ATTEMPTS` falhas. |
| **CA-17** | O OpenAPI lista todos os endpoints nativos com exemplos e documenta os dois tipos de resposta do chat. |
| **CA-18** | Cobertura de testes ≥ 80% e pipeline de lint/tipagem sem falhas. |
| **CA-19** | No navegador, o frontend lê `X-RateLimit-*`, `Retry-After` e `X-Request-ID` (CORS). |
| **CA-20** | O admin lista guests, obtém um `gid` e zera sua cota (`204`); alvo inexistente retorna `404`. |
| **CA-21** | A resposta é interrompida ao atingir `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS`, com `finish_reason` adequado. |
| **CA-22** | `401` traz `WWW-Authenticate: Bearer`; `422` segue o formato de 3.8 com `errors[]`. |
| **CA-23** | A API recusa iniciar com `UVICORN_WORKERS > 1` sem `REDIS_URL`. |
| **CA-24** | `GET /models` lista os modelos locais e de nuvem, com metadados, capacidades, provedor, modelo padrão e `selectable`. |
| **CA-25** | `POST /chat` aceita Model ID/alias instalado e usa o modelo resolvido na resposta e no SSE; modelo inexistente/fora da allowlist retorna `422` sem consumir cota. |
| **CA-26** | O gateway roteia `provider/model` para o adapter correto; nome sem prefixo usa o modelo padrão; modelo desconhecido retorna `422`. |
| **CA-27** | Os seis provedores estão registrados e a API recusa iniciar se um provedor habilitado não tiver credenciais/base URL. |
| **CA-28** | `GET /api/v1/models` agrega provedores, capacidades e `selectable`, com cache e `?refresh=true`. |
| **CA-29** | Incompatibilidade de capacidade (`tools`, `vision`, `json_mode`) retorna `422 unsupported_capability` sem consumir cota. |
| **CA-30** | O fallback ocorre somente antes do primeiro token, com uma única unidade de cota consumida. |
| **CA-31** | `POST /v1/chat/completions` sem stream retorna o shape OpenAI completo. |
| **CA-32** | `POST /v1/chat/completions` com stream retorna chunks `chat.completion.chunk` e termina com `data: [DONE]`. |
| **CA-33** | `GET /v1/models` retorna o shape `{"object":"list","data":[...]}`. |
| **CA-34** | `POST /v1/messages` sem stream retorna o shape Anthropic, incluindo blocos de conteúdo e `stop_reason`. |
| **CA-35** | `POST /v1/messages` com stream emite a sequência Anthropic completa e suporta `tool_use`. |
| **CA-36** | O Claude Code consegue usar a API como backend Anthropic (tools, system, streaming e `count_tokens`). |
| **CA-37** | `POST /api/chat` emite NDJSON e `GET /api/tags` retorna o shape Ollama. |
| **CA-38** | As rotas de compatibilidade exigem API key válida e aplicam cotas/rate limit; erros usam o envelope da superfície. |
| **CA-39** | Chaves de provedores e API keys NEXA não aparecem em respostas, logs ou erros. |
| **CA-40** | O login do admin funciona com o SQLite removido/indisponível (fallback de configuração). |
| **CA-41** | O admin não pode ser excluído, desativado ou rebaixado por API nem por SQL direto (triggers). |
| **CA-42** | Usuários de teste podem ser criados, autenticados, listados e excluídos; a limpeza atinge apenas `is_test = 1`. |
| **CA-43** | As metas de performance do SQLite (leitura p95 ≤ 2 ms, listagem de 10 mil usuários p95 ≤ 20 ms, escrita p95 ≤ 10 ms) são medidas e atendidas. |
| **CA-44** | Migrações preservam o admin, criam backup e a API recusa iniciar se o admin não existir após a migração. |
| **CA-45** | O SQLite contém apenas usuários/migrações; nenhum conteúdo de chat é gravado (revisão do CA-06). |
| **CA-46** | `README.md`, `.env.example`, SRS, plano, matriz e OpenAPI estão consistentes com a v2.0. |
| **CA-47** | O sistema passa nos testes de contrato de compatibilidade OpenAI, Anthropic e Ollama e nos testes de segurança de chaves. |

---

## 5. Apêndices

### 5.1 Itens fora do escopo (versão 2.0)

- Frontend React e seus requisitos.
- Histórico de conversas, mensagens salvas, fixar/arquivar, ramificação e feedback.
- Cadastro público, verificação de e-mail, recuperação de senha e SMTP.
- Refresh token, sessões por dispositivo e revogação de tokens no servidor.
- Auditoria persistente, estatísticas históricas e exportação de dados (LGPD).
- Cobrança, planos pagos e controle de custo por tokens.
- Login social, MFA, upload de arquivos, RAG e embeddings.
- Tabela de API keys no banco; gestão de chaves pelo admin.
- Suporte a múltiplos bancos além do SQLite.

### 5.2 Decisões e premissas adotadas neste SRS

| # | Decisão | Motivo |
|---|---|---|
| 1 | Gateway multi-provedor com Model ID `provider/model`. | Permite alternar entre local e nuvem sem alterar rotas e sem acoplar o cliente. |
| 2 | Camadas de compatibilidade OpenAI, Anthropic e Ollama. | Permite que sistemas existentes (Claude Code, Cursor, LangChain, clientes Ollama) usem a NEXA API sem adaptação. |
| 3 | SQLite apenas para usuários. | Atende à necessidade de usuários de teste com criação rápida, sem transformar a API em um sistema de histórico. |
| 4 | ADMIN permanente e inviolável, com fallback para configuração. | Garante acesso administrativo mesmo com banco perdido/corrompido. |
| 5 | Usuários de teste descartáveis. | Mantém o banco pequeno, rápido e sem risco de acumular dados de teste. |
| 6 | WAL + uma conexão de escrita + pool de leitura + cache. | SQLite é single-writer; essa combinação entrega leituras rápidas e escritas previsíveis. |
| 7 | API keys de compatibilidade em variável de ambiente. | Mantém o escopo do banco restrito a usuários e simplifica a operação inicial. |
| 8 | Fallback só antes do primeiro token. | Evita duplicar resposta/custo e mantém a cota consistente. |
| 9 | Erros de compatibilidade no envelope da API externa. | Clientes existentes esperam o formato original. |
| 10 | Nenhuma persistência de conversa em nenhuma superfície. | Preserva a decisão de chat efêmero e reduz risco/LGPD. |

### 5.3 Pontos em aberto

| # | Questão | Impacto |
|---|---|---|
| 1 | Qual o conjunto inicial de modelos/provedores em produção? | Define custos, latência e allowlist. |
| 2 | Qual o limite de cota para usuários de teste? | Ajuste de `TEST_USER_*`. |
| 3 | O fallback entre provedores deve ser habilitado por padrão? | Custo e previsibilidade de resposta. |
| 4 | Haverá mais de um nó da API antes do Redis? | Se sim, Redis passa a ser obrigatório para cotas. |
| 5 | A compatibilidade Ollama deve exigir API key em produção? | Clientes Ollama podem não enviar header; avaliar bypass local. |
| 6 | Qual o prazo de retenção de usuários de teste? | Define `TEST_USER_TTL_HOURS` e rotina de limpeza. |
| 7 | O `count_tokens` da Anthropic usará contagem real ou estimativa? | Afeta a fidelidade do Claude Code. |
| 8 | Qual o prazo máximo do graceful shutdown com streams abertos? | Evita desligamento travado. |

### 5.4 Resumo de rastreabilidade (módulo → endpoints)

| Módulo | Requisitos | Endpoints |
|---|---|---|
| Autenticação | RF-AUT-01 a 13 | `/api/v1/auth/*` |
| Chat nativo | RF-CHAT-01 a 17 | `/api/v1/chat` |
| Limites de uso | RF-USO-01 a 16 | `/api/v1/usage`, `/api/v1/chat` |
| Administração | RF-ADM-01 a 10 | `/api/v1/admin/*` |
| Sistema | RF-SIS-01 a 06 | `/health*`, `/docs`, `/redoc` |
| Gateway | RF-GTW-01 a 10 | `/api/v1/models`, `/api/v1/chat` |
| Compatibilidade | RF-CMP-01 a 12 | `/v1/*`, `/api/*` |
| Usuários/banco | RF-USR-01 a 15 | `/api/v1/auth/login`, `/api/v1/admin/users*` |

### 5.5 Migração da v1.1.0 para a v2.0

| Item | Ação |
|---|---|
| `AI_PROVIDER`, `CLOUD_*` | Descontinuados; converter para `AI_DEFAULT_MODEL` e remover após a fase F17. |
| `OLLAMA_MODEL` | Mantido como alias legado de `AI_DEFAULT_MODEL=ollama/<modelo>`. |
| Banco | Criar `./data/nexa.db`, aplicar migrações e semear o admin a partir da configuração. |
| Usuários existentes | Não existem na v1.1.0; criar usuários de teste via `/admin/users`. |
| Tokens emitidos | Continuam válidos até expirar; o `JWT_SECRET` não muda. |
| Contadores | Continuam em memória; reiniciar zera as cotas. |
| Clientes nativos | O contrato v2 adiciona `model_id` e `provider_usage`, mas mantém os campos principais; clientes antigos devem ser revalidados. |
| Rotas | As rotas nativas permanecem; as rotas de compatibilidade são novas. |

### 5.6 Preparação para a próxima versão

| Item | Como a v2.0 se prepara |
|---|---|
| Histórico de conversas | O chat já recebe o contexto completo e devolve `message`; basta acrescentar `conversation_id` e uma tabela própria (fora do escopo atual). |
| API keys no banco | A autenticação de compatibilidade já está isolada; basta trocar o provedor de chaves. |
| Múltiplos nós | `UsageStore` e `UserRepository` estão atrás de interfaces; Redis e um banco compartilhado podem entrar sem alterar rotas. |
| Auditoria | Os eventos de segurança já são logados; uma tabela de auditoria pode ser adicionada sem tocar no chat. |
| Billing | `provider_usage` já expõe tokens por requisição. |
| Novos provedores | O registry de adapters permite adicionar provedores sem alterar rotas. |
