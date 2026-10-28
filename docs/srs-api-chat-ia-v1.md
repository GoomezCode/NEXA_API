# Especificação de Requisitos de Software (SRS)

## API do Chat de IA via Web

| Campo | Valor |
|---|---|
| **Projeto** | Chat de IA via Web: Backend (API) |
| **Documento** | SRS: Especificação de Requisitos de Software |
| **Versão** | 1.1 (rascunho) |
| **Data** | 28/10/2026 |
| **Padrões de referência** | IEEE Std 830-1998, ISO/IEC/IEEE 29148:2018 |
| **Documento de origem** | `levantamento-chat-ia-web.md` (documento externo a este repositório) |

### Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 1.1 | 28/10/2026 | Revisão de consistência após análise do SRS: contrato de SSE explicitado (consumo via `fetch`, não `EventSource`); limite de tokens de saída; descoberta e reset de `gid` no admin; semântica única de cota e de `usage`; schemas de `/admin/*` e `/health*`; variáveis de ambiente faltantes; separação `AI_PROVIDER`/`CLOUD_PROVIDER`; CORS com `expose_headers`; remoção da exceção de log de conteúdo; prioridades alinhadas aos critérios de aceitação |
| 1.0 | 28/10/2026 | Primeira versão do SRS da API, **sem banco de dados**. Existe um único usuário salvo (**ADMIN**, com senha, vindo da configuração) e usuários **ghost** (anônimos) com limite de uso. A persistência fica para a próxima versão da API (seção 5.5) |

> **Resumo da versão atual:** a API é **stateless e sem banco**. O chat é efêmero (nada é gravado), o administrador é definido por variáveis de ambiente e o visitante anônimo (guest, informalmente "ghost") usa o chat dentro de limites de uso. Cadastro público, perfil, histórico de conversas, feedback, verificação de e-mail, recuperação de senha e gestão de usuários **ficam para a próxima versão**, junto com o banco (seção 5.5).

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

Este documento especifica os requisitos da **API de backend** de um sistema de chat de IA via web, na sua primeira etapa **sem banco de dados**. Destina-se a desenvolvedores, testadores e demais envolvidos no projeto, e serve de base para o projeto técnico, a implementação, os testes e a validação da API.

### 1.2 Escopo

O produto especificado é a **API REST** (em Python/FastAPI) que:

- autentica o **administrador** (único usuário salvo, definido na configuração);
- permite o uso do chat por **usuários guest** (anônimos, também chamados de ghost), com **limites de uso**;
- recebe perguntas e devolve respostas de um modelo de IA, com **streaming**;
- oferece ao administrador consultas simples de monitoramento (estatísticas em memória e status do provedor de IA);
- abstrai o provedor de IA, de modo que a troca de **modelo local (Ollama)** para **nuvem** seja feita apenas por configuração.

**Fora do escopo deste documento:** o frontend React, a infraestrutura de hospedagem, a escolha e o treinamento dos modelos de IA, o faturamento e **todo o armazenamento persistente** (banco de dados, histórico de conversas, cadastro de usuários), que ficam para a próxima versão da API.

### 1.3 Definições, siglas e abreviações

| Termo | Definição |
|---|---|
| **API** | Interface de programação de aplicações |
| **REST** | Estilo arquitetural para APIs sobre HTTP |
| **SRS** | Software Requirements Specification |
| **JWT** | JSON Web Token, token assinado usado para autenticação |
| **SSE** | Server-Sent Events, fluxo de eventos do servidor para o cliente sobre HTTP |
| **Provedor de IA** | Implementação concreta que fala com um modelo (Ollama ou nuvem) |
| **Strategy** | Padrão de projeto que permite trocar algoritmos/implementações via interface comum |
| **Admin** | Único usuário salvo; credenciais definidas por variáveis de ambiente |
| **Ghost** | Usuário anônimo, sem cadastro nem senha, identificado por um token de convidado |
| **Guest** | Termo técnico canônico do usuário anônimo: papel JWT `guest` e prefixo `GUEST_*` nas variáveis. "Ghost" é sinônimo informal e "visitante" é o rótulo de interface |
| **Cota efetiva** | Menor valor entre a cota restante por `gid` e a cota restante por IP (RF-USO-04, RF-USO-12) |
| **Unidade de cota** | Uma requisição a `POST /chat` consome exatamente 1 unidade, independentemente do número de mensagens do contexto (RF-USO-11) |
| **Token de convidado** | JWT emitido a um guest por `POST /auth/guest` |
| **Cota** | Quantidade de mensagens que um guest pode enviar em uma janela de tempo |
| **Stateless** | Sem estado persistido no servidor; o que existe fica apenas em memória e se perde ao reiniciar |
| **RF / RNF / RN / RD** | Requisito Funcional / Não Funcional / Regra de Negócio / Requisito de Dados (estado e configuração) |

**Prioridades (MoSCoW):** **M** = Deve (Must), **S** = Deveria (Should), **C** = Poderia (Could).

### 1.4 Referências

- `levantamento-chat-ia-web.md`: documento de planejamento do projeto.
- IEEE Std 830-1998: *Recommended Practice for Software Requirements Specifications*.
- ISO/IEC/IEEE 29148:2018: *Requirements engineering*.
- RFC 7519 (JWT), RFC 6750 (Bearer Token), RFC 9457 (Problem Details for HTTP APIs).
- Documentação do FastAPI e do Ollama.

### 1.5 Visão geral do documento

A seção 2 descreve o produto e seu contexto. A seção 3 traz os requisitos detalhados. A seção 4 define os critérios de aceitação e a seção 5 reúne apêndices, incluindo o mapa do que foi removido e o que retorna na próxima versão.

---

## 2. Descrição geral

### 2.1 Perspectiva do produto

A API é o componente central do sistema e fica entre o frontend e o provedor de IA:

```
[Frontend React] ⇄ [API Python (FastAPI)] ⇄ [Provedor de IA: Local (Ollama) ou Nuvem (API)]
                          │
                          └─ estado somente em memória (contadores de uso)
```

O frontend **nunca** se comunica diretamente com a IA. Somente a API o faz, o que garante proteção da chave da API de nuvem, controle de acesso, limites de uso e independência do frontend em relação ao modelo usado.

**Não há banco de dados.** O único dado "salvo" é a credencial do administrador, lida das variáveis de ambiente na inicialização. O histórico da conversa é responsabilidade do frontend, que reenvia o contexto a cada requisição.

### 2.2 Funções do produto (resumo)

| Módulo | Funções principais |
|---|---|
| **Autenticação** | Login do admin; emissão de token de convidado (guest) |
| **Chat** | Enviar mensagens e receber resposta completa ou em streaming (efêmero, sem persistência) |
| **Limites de uso** | Cota diária e limite por minuto para guest; consulta do uso restante |
| **Administração** | Estatísticas de uso em memória, status do provedor de IA e gestão da cota dos guests |
| **Sistema** | Health check e documentação automática |

### 2.3 Classes de usuários e características

| Classe | Descrição | Permissões |
|---|---|---|
| **Guest** (ghost) | Visitante anônimo com token de convidado | Chat, consulta do próprio uso, **sujeito a limites de uso** |
| **Administrador** | Autenticado com usuário e senha, papel `admin` | Tudo do guest, **sem cota diária** (mantém limites técnicos de proteção) + endpoints `/admin/*` |
| **Cliente de API (frontend)** | Aplicação React que consome a API em nome do usuário | Mesmas permissões de quem representa |

### 2.4 Ambiente operacional

- **Linguagem/runtime:** Python 3.11 ou superior.
- **Framework:** FastAPI (ASGI, executado com Uvicorn).
- **IA local:** Ollama (padrão `http://localhost:11434`).
- **IA em nuvem (futuro):** provedor via SDK/HTTP (Anthropic, OpenAI, Google, entre outros).
- **Armazenamento:** nenhum. Contadores de uso ficam em memória (nó único e **processo único**) ou em Redis (vários nós/processos, ver RP-07).
- **Sistemas operacionais:** Linux (produção); Windows, macOS e Linux (desenvolvimento).

### 2.5 Restrições gerais

- Backend obrigatoriamente em **Python/FastAPI**.
- **Nenhum banco de dados** nesta versão.
- Troca de provedor de IA **somente por variável de ambiente**, sem alteração de código nos endpoints.
- Modelos de IA **não possuem memória**: o contexto é enviado a cada requisição.
- Desempenho da IA local depende do hardware (CPU/GPU/VRAM) disponível.
- Contadores em memória são **voláteis**: reiniciar a API zera os limites de uso (ver RN-12).
- Os contadores em memória exigem **um único processo** (`UVICORN_WORKERS=1`); sem Redis, a API recusa múltiplos processos (RP-07, RNF-CON-06).
- O streaming do chat exige `fetch`/`ReadableStream` no cliente; `EventSource` não é compatível com `POST` + `Authorization` (RP-09).

### 2.6 Premissas e dependências

- O Ollama (ou provedor de nuvem) está instalado, acessível pela rede e com o modelo configurado já baixado.
- O frontend consome a API via HTTPS em produção.
- O frontend envia a **lista completa de mensagens** da conversa a cada requisição de chat e guarda o histórico por conta própria.
- As credenciais do administrador e o segredo do JWT são fornecidos por variáveis de ambiente no deploy.
- Em produção, modelo local exige servidor com GPU, o que pode tornar a nuvem mais econômica.

---

## 3. Requisitos específicos

### 3.1 Interfaces externas

#### 3.1.1 Interface com o cliente (API HTTP)

| Aspecto | Especificação |
|---|---|
| Protocolo | HTTP/1.1 ou superior; HTTPS obrigatório em produção |
| Estilo | REST, JSON (`application/json`, UTF-8), campos em `snake_case` e em inglês |
| Streaming | SSE (`text/event-stream`) no endpoint de chat, com `Cache-Control: no-cache`, `Connection: keep-alive` e `X-Accel-Buffering: no`; heartbeat `: ping` a cada 15 s de inatividade |
| Autenticação | Header `Authorization: Bearer <token>` |
| Prefixo e versão | `/api/v1` |
| Documentação | OpenAPI/Swagger em `/docs` e ReDoc em `/redoc` |
| Datas e horas | ISO 8601 em UTC (ex.: `2026-10-28T14:30:00Z`) |
| Rastreabilidade | `X-Request-ID` em todas as respostas (RF-SIS-03) |
| CORS | Origens de `CORS_ORIGINS`, com `expose_headers` para `X-RateLimit-*`, `Retry-After`, `X-Request-ID` e `Content-Type` (RNF-SEG-04) |

**Nota sobre o streaming no cliente:** o endpoint de chat é `POST` e exige `Authorization`, portanto **não pode ser consumido com `EventSource`** (que só faz `GET` e não envia cabeçalhos). O frontend deve usar `fetch` com `ReadableStream` sobre `response.body` e interpretar o formato SSE descrito em 3.7.3.

#### 3.1.2 Interface com provedores de IA

- Toda comunicação com IA passa pela interface abstrata `AIProvider` (métodos `generate` e `stream`), cujo retorno inclui o texto, o `finish_reason` (`stop`, `length`, `timeout`) e, quando o provedor informar, a contagem de tokens.
- **OllamaProvider:** `POST {OLLAMA_URL}/api/chat` (JSON; streaming por linhas JSON).
- **CloudProvider:** SDK/HTTP do provedor escolhido (`CLOUD_PROVIDER`), **não implementado nesta versão** — apenas contrato/esqueleto (§5.1).
- Formato de mensagens padrão: `[{"role": "user" | "assistant" | "system", "content": "..."}]`.
- Os limites `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS` são aplicados pela API, independentemente do provedor (RF-CHAT-12).

#### 3.1.3 Interfaces de hardware

Não há interface direta de hardware. O desempenho da IA local depende de CPU (16 GB de RAM para modelos de 1B a 8B parâmetros) e, preferencialmente, de GPU.

#### 3.1.4 Configuração (variáveis de ambiente)

| Variável | Descrição | Exemplo |
|---|---|---|
| `AI_PROVIDER` | Provedor ativo (`ollama` ou `cloud`) | `ollama` |
| `OLLAMA_URL` | URL do servidor Ollama | `http://localhost:11434` |
| `OLLAMA_MODEL` | Modelo local | `llama3.1` |
| `CLOUD_API_KEY` | Chave do provedor de nuvem | (vazio até uso) |
| `CLOUD_PROVIDER` | Fornecedor de nuvem quando `AI_PROVIDER=cloud` (`openai`, `anthropic`, `google`) | (vazio até uso) |
| `CLOUD_BASE_URL` | (Opcional) URL base do provedor de nuvem | (vazio) |
| `CLOUD_MODEL` | Modelo de nuvem | (vazio até uso) |
| `ADMIN_USERNAME` | Nome de usuário do administrador | `admin` |
| `ADMIN_PASSWORD_HASH` | Hash (argon2 ou bcrypt) da senha do administrador | (gerado no deploy) |
| `JWT_SECRET` | Segredo de assinatura do JWT | (valor aleatório forte) |
| `JWT_ALGORITHM` | Algoritmo de assinatura | `HS256` |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | Validade do token do admin | `60` |
| `GUEST_TOKEN_EXPIRE_HOURS` | Validade do token de convidado | `24` |
| `GUEST_DAILY_MESSAGES` | Cota diária de mensagens por guest | `10` |
| `GUEST_RATE_PER_MIN` | Limite de mensagens por minuto por guest | `3` |
| `GUEST_MAX_MESSAGES_PER_REQUEST` | Máx. de mensagens no contexto (guest) | `20` |
| `GUEST_MAX_MESSAGE_CHARS` | Máx. de caracteres por mensagem (guest) | `2000` |
| `GUEST_IP_DAILY_MESSAGES` | Cota diária somada por IP (impede burlar a cota trocando de token) | `30` |
| `GUEST_TOKENS_PER_IP_PER_HOUR` | Tokens de convidado emitidos por IP por hora | `10` |
| `RATE_LIMIT_ADMIN_CHAT_PER_MIN` | Limite técnico de chat por minuto do admin | `60` |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | Tentativas de login antes do bloqueio / duração do bloqueio | `5` / `15` |
| `MAX_MESSAGES_PER_REQUEST` | Máx. de mensagens no contexto (admin) | `100` |
| `MAX_MESSAGE_CHARS` | Máx. de caracteres por mensagem (admin) | `8000` |
| `ALLOW_CLIENT_SYSTEM_MESSAGES` | Aceita mensagens `system` enviadas pelo cliente (RN-06) | `false` |
| `DEFAULT_SYSTEM_PROMPT` | Prompt de sistema padrão do servidor | (texto) |
| `CORS_ORIGINS` | Origens permitidas | `http://localhost:5173` |
| `ENVIRONMENT` | Ambiente (`development` ou `production`); em `production` aplicam-se RNF-SEG-03 e RNF-SEG-12 | `development` |
| `AI_TIMEOUT_SECONDS` | Timeout de chamadas não-streaming ao provedor de IA | `120` |
| `AI_STREAM_IDLE_TIMEOUT_SECONDS` | Inatividade máxima no streaming antes de abortar | `60` |
| `AI_MAX_OUTPUT_TOKENS` | Máx. de tokens de saída por resposta (definido pelo servidor; o cliente não escolhe) | `1024` |
| `AI_MAX_OUTPUT_SECONDS` | Tempo máximo de geração de uma única resposta (guarda-corpo) | `300` |
| `MAX_REQUEST_BODY_BYTES` | Tamanho máximo do corpo da requisição; deve ser compatível com os limites do papel (RNF-SEG-09) | `2097152` |
| `TRUSTED_PROXIES` | IPs/CIDRs de proxies confiáveis para ler `X-Forwarded-For` (vazio = não confiar no cabeçalho) | `127.0.0.1/32` |
| `UVICORN_WORKERS` | Nº de processos da API; **deve ser `1`** quando não houver Redis (RP-07) | `1` |
| `LOG_LEVEL` | Nível de log | `INFO` |
| `REDIS_URL` | (Opcional) Redis para contadores compartilhados entre vários nós | (vazio) |

Toda a configuração deve ser validada na inicialização (tipo, faixa e obrigatoriedade), e a API deve **recusar-se a iniciar** com valores inválidos, além das regras específicas de RF-AUT-01 e RNF-SEG-12.

---

### 3.2 Requisitos funcionais

#### 3.2.1 Autenticação (RF-AUT)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-AUT-01** | Na inicialização, a API deve carregar `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` da configuração e **recusar-se a iniciar** se qualquer um estiver ausente ou se o hash não for válido. Esse é o único usuário salvo do sistema. | M |
| **RF-AUT-02** | A API deve permitir **login do administrador** com `username` e `password`, retornando um **access token JWT**, o tipo do token e o tempo de expiração. | M |
| **RF-AUT-03** | A senha do administrador nunca deve ser mantida em texto puro: a verificação compara a senha recebida com o hash (`argon2` ou `bcrypt`) da configuração, em tempo constante. | M |
| **RF-AUT-04** | Credenciais inválidas devem retornar `401 Unauthorized` com mensagem genérica, sem revelar se o usuário existe. O tempo de resposta deve ser equivalente para usuário inexistente e senha incorreta. | M |
| **RF-AUT-05** | A API deve oferecer **emissão de token de convidado** (`POST /auth/guest`), sem credenciais. Cada chamada gera um identificador de convidado (`gid`, UUID v4) e um JWT com papel `guest`. | M |
| **RF-AUT-06** | O access token JWT deve conter, no mínimo, `sub` (nome do admin ou `gid`), `role` (`admin` ou `guest`), `iat` e `exp`. | M |
| **RF-AUT-07** | Todos os endpoints, exceto os públicos (login, token de convidado, health, documentação), devem exigir token válido e não expirado. | M |
| **RF-AUT-08** | A emissão de tokens de convidado deve ser limitada por IP (`GUEST_TOKENS_PER_IP_PER_HOUR`), retornando `429` com o código `guest_token_limit_reached` e o cabeçalho `Retry-After` ao exceder. | M |
| **RF-AUT-09** | Nesta versão não há refresh token nem revogação no servidor: o **logout** é feito pelo cliente descartando o token. O token do admin tem validade curta e configurável. | S |
| **RF-AUT-10** | Tokens com assinatura inválida, expirados ou com papel desconhecido devem retornar `401`. A resposta deve incluir o cabeçalho `WWW-Authenticate: Bearer` (RFC 6750). | M |

#### 3.2.2 Chat (RF-CHAT)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-CHAT-01** | A API deve expor endpoint de chat que recebe a **lista completa de mensagens** (`messages`) da conversa e devolve a resposta do modelo de IA. | M |
| **RF-CHAT-02** | O endpoint deve suportar dois modos, controlados pelo campo `stream` (padrão `true`): **streaming via SSE** e **resposta completa em JSON**. | M |
| **RF-CHAT-03** | No modo streaming, a resposta deve ser enviada em eventos SSE incrementais (pedaços de texto), com evento final de conclusão e evento de erro quando aplicável (ver 3.7.3). A resposta deve declarar `Content-Type: text/event-stream` e os cabeçalhos de cache/proxy de 3.1.1, além de enviar heartbeat (`: ping`) a cada 15 s de inatividade. | M |
| **RF-CHAT-04** | O endpoint deve validar a entrada: `messages` não vazio; cada item com `role` ∈ {`system`, `user`, `assistant`} e `content` não vazio (desconsiderando espaços); a última mensagem deve ter `role = user`; respeitar os limites de mensagens e de caracteres do papel do chamador (3.1.4). | M |
| **RF-CHAT-05** | O endpoint de chat não deve conhecer o provedor concreto: deve usar apenas a interface `AIProvider`, obtida via *factory* a partir de `AI_PROVIDER` (e `CLOUD_PROVIDER`, quando for o caso). | M |
| **RF-CHAT-06** | O chat é **efêmero**: nenhuma mensagem, pergunta ou resposta é gravada pela API (nem em arquivo, nem em banco). O conteúdo existe apenas durante o processamento da requisição. | M |
| **RF-CHAT-07** | Se o cliente interromper a conexão durante o streaming, a API deve cancelar a requisição ao provedor de IA e liberar recursos. Se a interrupção ocorrer antes do primeiro trecho, aplica-se a devolução de cota de RF-USO-05. | S |
| **RF-CHAT-08** | Falhas do provedor de IA (indisponível, timeout, erro de modelo) devem ser tratadas e traduzidas para `502` (`ai_provider_error`) ou `504` (`ai_provider_timeout`); se o streaming já iniciou, para um evento SSE `error` com o mesmo `code` (ver 3.7.3). | M |
| **RF-CHAT-09** | O endpoint **não** deve aceitar um parâmetro separado `system_prompt` nem parâmetros de geração enviados pelo cliente. A orientação do sistema é feita exclusivamente por mensagens `role = system`, conforme RN-06, e os parâmetros de geração são definidos pelo servidor (`AI_MAX_OUTPUT_TOKENS`, `AI_MAX_OUTPUT_SECONDS`). | M |
| **RF-CHAT-10** | As mensagens `system` recebidas do cliente e o prompt de sistema padrão são usados apenas no contexto enviado à IA. | M |
| **RF-CHAT-11** | Antes de chamar o provedor de IA, o endpoint deve aplicar os limites de uso do chamador (seção 3.2.3). Requisição barrada pelo limite **não** chega ao provedor. | M |
| **RF-CHAT-12** | A geração deve ser limitada por `AI_MAX_OUTPUT_TOKENS` e `AI_MAX_OUTPUT_SECONDS`, aplicados pela API ao provedor. Ao atingir qualquer um dos limites, a resposta termina normalmente com `finish_reason` = `length` ou `timeout`, tanto em JSON quanto no evento `done` do SSE. | M |

#### 3.2.3 Limites de uso (RF-USO)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-USO-01** | O guest deve ter uma **cota diária** de mensagens (`GUEST_DAILY_MESSAGES`). Ao esgotá-la, `POST /chat` retorna `429` com o código `guest_daily_limit_reached` e o instante de reinício da cota em `resets_at` (ISO 8601 UTC). | M |
| **RF-USO-02** | O guest deve ter um **limite por minuto** (`GUEST_RATE_PER_MIN`), em janela deslizante de 60 s. Ao excedê-lo, retornar `429` com `Retry-After` (em segundos) e código `rate_limited`. | M |
| **RF-USO-03** | O guest deve ter limites de **tamanho de contexto** menores que os do admin (`GUEST_MAX_MESSAGES_PER_REQUEST`, `GUEST_MAX_MESSAGE_CHARS`). Ao excedê-los, retornar `422` com `errors[]` (3.8). | M |
| **RF-USO-04** | Além da cota por `gid`, a API deve aplicar uma **cota diária por IP** (`GUEST_IP_DAILY_MESSAGES`), somando todos os guests do mesmo IP. A cota efetiva é o menor valor restante entre `gid` e IP (RF-USO-12). Isso impede burlar a cota pedindo novos tokens. | M |
| **RF-USO-05** | Uma mensagem é contabilizada **ao iniciar** a geração. Se o provedor de IA falhar antes do primeiro trecho de resposta, a contagem deve ser **devolvida** à cota do `gid` **e** à do IP. Considera-se "primeiro trecho" o primeiro evento SSE `data:` com conteúdo não vazio (ou o primeiro token em modo JSON). | M |
| **RF-USO-06** | O administrador não possui cota diária, mas mantém um limite técnico por minuto (`RATE_LIMIT_ADMIN_CHAT_PER_MIN`) para proteger o provedor de IA. | M |
| **RF-USO-07** | A API deve oferecer `GET /usage`, que retorna ao chamador seus limites e o uso restante (`daily_limit`, `daily_remaining`, `per_minute_limit`, `per_minute_remaining`, `resets_at`). Para o admin, `daily_limit`, `daily_remaining` e `resets_at` são `null`. | M |
| **RF-USO-08** | Toda resposta de `POST /chat` **de guest** deve incluir `X-RateLimit-Limit`, `X-RateLimit-Remaining` e `X-RateLimit-Reset`, referentes à cota efetiva diária; `X-RateLimit-Reset` é expresso em *epoch seconds* UTC. Esses cabeçalhos também devem acompanhar o `429`, junto de `Retry-After`. Para o admin, os cabeçalhos de cota diária são omitidos. | M |
| **RF-USO-09** | A cota diária reinicia à 00:00 UTC (janela fixa). | M |
| **RF-USO-10** | O administrador deve poder **zerar a cota** de um guest (`gid`) ou de um IP, por endpoint administrativo (3.7.4), usando a listagem de RF-ADM-05 para obter o `gid`. | M |
| **RF-USO-11** | Uma requisição de chat consome exatamente **1 unidade** de cota, independentemente do número de mensagens do contexto. | M |
| **RF-USO-12** | Quando a cota por IP for a mais restritiva, o código de erro é `ip_daily_limit_reached`; quando a cota por `gid` for a mais restritiva, é `guest_daily_limit_reached`. A verificação das duas cotas deve ser atômica por chave, para não permitir duas requisições concorrentes consumirem a mesma unidade. | M |

#### 3.2.4 Administração (RF-ADM)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-ADM-01** | Todos os endpoints `/admin/*` devem ser acessíveis somente com token de papel `admin`; guest recebe `403 Forbidden`. | M |
| **RF-ADM-02** | A API deve fornecer **estatísticas de uso em memória**, desde a última inicialização: total de requisições de chat, quantidade de guests distintos, mensagens barradas por limite, erros do provedor e média de duração das respostas. A contagem de guests distintos deve usar estrutura de memória limitada (ex.: HyperLogLog ou conjunto com expiração), para não crescer indefinidamente. | S |
| **RF-ADM-03** | A API deve informar o **status do provedor de IA** ativo (nome, modelo configurado e disponibilidade), sem expor chaves ou segredos. | S |
| **RF-ADM-04** | A API deve permitir ao administrador **zerar a cota** de um guest ou IP (RF-USO-10), com o corpo definido em 3.7.4. | M |
| **RF-ADM-05** | A API deve permitir ao administrador **listar os guests ativos em memória** (`gid`, IP, uso diário e instante da última atividade), para tornar operável o reset por `gid` (RF-ADM-04). | M |

> Gestão de usuários, suspensão, redefinição de senha, log de auditoria persistente e estatísticas históricas **não existem** nesta versão: dependem de banco de dados (seção 5.5).

#### 3.2.5 Sistema (RF-SIS)

| ID | Requisito | Prior. |
|---|---|---|
| **RF-SIS-01** | A API deve expor `GET /health` (público) indicando se o serviço está no ar, com o corpo definido em 3.7.5. | M |
| **RF-SIS-02** | A API deve expor documentação interativa OpenAPI automática. | M |
| **RF-SIS-03** | A API deve incluir um identificador de requisição (`X-Request-ID`) em todas as respostas, para rastreabilidade. Se o cliente enviar um `X-Request-ID` válido (UUID), ele deve ser aceito e propagado; caso contrário, a API gera um novo. O mesmo valor deve aparecer no corpo dos erros. | M |
| **RF-SIS-04** | O health check deve poder incluir a disponibilidade do provedor de IA (`GET /health/ready`). | C |

---

### 3.3 Requisitos de estado e configuração

Sem banco de dados, o "dado" da API se resume à **configuração** e ao **estado volátil em memória**.

| ID | Requisito |
|---|---|
| **RD-01** | A API não deve criar, ler nem escrever em nenhum banco de dados nesta versão. Não há ORM, migrações nem string de conexão. |
| **RD-02** | A única credencial de usuário é a do administrador, vinda de `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH`. Nunca deve ser escrita em logs nem retornada em respostas. |
| **RD-03** | Os contadores de uso (por `gid`, por IP, por minuto) e de tentativas de login ficam **em memória** (ou em Redis, quando `REDIS_URL` estiver definido) e **expiram sozinhos** ao fim da janela, para não crescer indefinidamente. Os contadores em memória pressupõem **um único processo** (`UVICORN_WORKERS=1`, RP-07); com mais de um processo/worker, o Redis passa a ser obrigatório. |
| **RD-04** | O conteúdo das mensagens não é armazenado. Logs e métricas guardam apenas metadados (papel, duração, tamanho, status). |
| **RD-05** | O `gid` do guest é um UUID v4 gerado pela API e embutido no token; o servidor não mantém lista de tokens emitidos. |
| **RD-06** | IPs usados nos contadores devem ser obtidos corretamente atrás de proxy reverso: o cabeçalho `X-Forwarded-For` só pode ser considerado quando a conexão vier de um IP listado em `TRUSTED_PROXIES`; caso contrário, usa-se o IP da conexão. O IP é descartado ao fim da janela. |
| **RD-07** | Datas devem ser geradas e retornadas em UTC. `resets_at` usa ISO 8601; `X-RateLimit-Reset` usa *epoch seconds*. |
| **RD-08** | O objeto `usage` tem um único schema (3.7.2) e deve ser idêntico em todas as respostas que o exponham (token de convidado, `/usage`, resposta JSON do chat e evento `done`). |

---

### 3.4 Requisitos não funcionais

#### 3.4.1 Desempenho

| ID | Requisito |
|---|---|
| **RNF-DES-01** | Endpoints sem IA (token de convidado, uso, admin, health) devem responder em até **100 ms** no percentil 95, em condições normais de carga (sem banco, o custo é só computacional). `/auth/login` tem meta própria de **300 ms** p95, por incluir deliberadamente a verificação de hash (RNF-SEG-01). |
| **RNF-DES-02** | O overhead da API sobre o provedor de IA no início do streaming deve ser inferior a **200 ms**; o tempo até o primeiro token depende do provedor/hardware. |
| **RNF-DES-03** | A API deve ser assíncrona (`async`) e não bloquear o *event loop* em chamadas de IA ou de rede. A verificação de hash de senha (custosa por projeto) deve rodar fora do *event loop*. |
| **RNF-DES-04** | A API deve suportar no mínimo **50 conexões de streaming simultâneas** em um único nó, limitada pela capacidade do provedor de IA. O número é uma meta de projeto, não um valor medido; o resultado real depende do hardware e do modelo (ponto em aberto 1 da seção 5.3). |
| **RNF-DES-05** | A checagem de limites de uso deve ter custo O(1) por requisição, com estruturas por chave limitadas ao tamanho da janela (o limite por minuto mantém no máximo `GUEST_RATE_PER_MIN` + 1 marcas de tempo por chave). |

#### 3.4.2 Segurança

| ID | Requisito |
|---|---|
| **RNF-SEG-01** | Hash da senha do admin com **argon2** (preferencial) ou **bcrypt**, com *salt*. |
| **RNF-SEG-02** | Access token com validade curta e configurável (`ADMIN_TOKEN_EXPIRE_MINUTES`, `GUEST_TOKEN_EXPIRE_HOURS`). |
| **RNF-SEG-03** | Em produção, todo o tráfego deve ocorrer sobre **HTTPS/TLS**. |
| **RNF-SEG-04** | **CORS** restrito às origens configuradas em `CORS_ORIGINS`, com preflight (`OPTIONS`) respondido e `Access-Control-Expose-Headers` cobrindo `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`, `X-Request-ID` e `Content-Type`. |
| **RNF-SEG-05** | Proteção contra força bruta no login do admin: limite de tentativas por IP (`LOGIN_MAX_ATTEMPTS`), com bloqueio temporário (`LOGIN_LOCK_MINUTES`) e resposta `429` com código `login_locked` e `Retry-After`. |
| **RNF-SEG-06** | Validação rigorosa de entrada com Pydantic; rejeitar campos desconhecidos nos corpos sensíveis (`extra="forbid"`). |
| **RNF-SEG-07** | Segredos (`JWT_SECRET`, `ADMIN_PASSWORD_HASH`, `CLOUD_API_KEY`) somente em variáveis de ambiente ou cofre de segredos; nunca no repositório. |
| **RNF-SEG-08** | A chave da API de nuvem nunca deve ser exposta ao cliente nem aparecer em logs ou respostas. |
| **RNF-SEG-09** | Limite de tamanho do corpo da requisição (`MAX_REQUEST_BODY_BYTES`, padrão 2 MB) para evitar abuso. O valor padrão deve ser compatível com o maior payload permitido pelos limites do papel (`MAX_MESSAGES_PER_REQUEST` × `MAX_MESSAGE_CHARS` + margem de serialização), de modo que uma requisição válida nunca receba `413`. |
| **RNF-SEG-10** | Cabeçalhos de segurança básicos nas respostas (ex.: `X-Content-Type-Options: nosniff`). |
| **RNF-SEG-11** | O conteúdo gerado pela IA deve ser tratado como dado não confiável; a API não deve executá-lo nem interpretá-lo como instruções. |
| **RNF-SEG-12** | O `JWT_SECRET` deve ter no mínimo 32 bytes de entropia; com `ENVIRONMENT=production`, a API deve recusar-se a iniciar com segredo fraco ou de exemplo (e com `ADMIN_PASSWORD_HASH` ausente ou inválido, RF-AUT-01). |
| **RNF-SEG-13** | Como o guest é anônimo e seu token é facilmente descartável, **nenhum recurso privilegiado** pode ser acessível com token de convidado. A defesa contra abuso é a cota por IP (RF-USO-04) somada ao limite de emissão de tokens (RF-AUT-08). |

#### 3.4.3 Confiabilidade e disponibilidade

| ID | Requisito |
|---|---|
| **RNF-CON-01** | Timeout configurável nas chamadas ao provedor de IA (`AI_TIMEOUT_SECONDS`, padrão 120 s para resposta completa); no streaming, sem limite total de duração, mas com detecção de inatividade (`AI_STREAM_IDLE_TIMEOUT_SECONDS`, padrão 60 s sem dados) e com os limites de saída de RF-CHAT-12 (`AI_MAX_OUTPUT_TOKENS`, `AI_MAX_OUTPUT_SECONDS`). |
| **RNF-CON-02** | Falhas do provedor de IA não devem derrubar a API nem afetar endpoints que não dependem dela (login, `/usage`, `/health`). |
| **RNF-CON-03** | Disponibilidade-alvo de **99%** mensal para a API (excluindo indisponibilidade do provedor de IA). Como a v1.1 opera em nó único sem HA, a meta pressupõe reinício automático pelo supervisor; indisponibilidade por deploy/restart conta contra a meta. |
| **RNF-CON-04** | Encerramento gracioso (*graceful shutdown*): permitir a conclusão de requisições em andamento (inclusive streams) antes de parar, respeitando um prazo máximo configurável. |
| **RNF-CON-05** | A API deve iniciar sem nenhuma dependência externa além do provedor de IA (e do Redis, se configurado). |
| **RNF-CON-06** | Com contadores em memória, a API deve operar com **um único processo**; a aplicação deve recusar-se a iniciar com `UVICORN_WORKERS > 1` quando `REDIS_URL` não estiver definido (RP-07, RD-03). |

#### 3.4.4 Manutenibilidade e extensibilidade

| ID | Requisito |
|---|---|
| **RNF-MAN-01** | Adoção do padrão **Strategy** para provedores de IA: interface `AIProvider` com `generate` e `stream`; implementações `OllamaProvider` e `CloudProvider`; escolha via `factory.py`. |
| **RNF-MAN-02** | Adicionar um novo provedor de IA deve exigir apenas: nova classe que implemente `AIProvider`, novo ramo na *factory* e novas variáveis no `.env`. |
| **RNF-MAN-03** | Estrutura modular do projeto: `api/` (rotas), `core/` (segurança e limites de uso), `ai/` (provedores), `config.py` (configuração). |
| **RNF-MAN-04** | O armazenamento de contadores deve ficar atrás de uma interface (ex.: `UsageStore`) com implementação em memória e, opcionalmente, Redis, para trocar sem alterar as rotas. |
| **RNF-MAN-05** | A camada de autenticação deve ser isolada em um módulo (ex.: `core/auth.py`), de modo que a troca da fonte do usuário (configuração → banco) na próxima versão não altere as rotas. |
| **RNF-MAN-06** | Cobertura mínima de testes automatizados de **80%** nas camadas de serviço e rotas. |
| **RNF-MAN-07** | Código aderente à PEP 8, com *linter* e formatador (ex.: Ruff/Black) e tipagem estática (mypy) no CI. |
| **RNF-MAN-08** | Dependências com versões fixadas em `requirements.txt` (e *lock* gerado por `pip-tools`/`uv`). |
| **RNF-MAN-09** | O `CloudProvider` existe nesta versão apenas como contrato/esqueleto (§5.1); sua seleção vem de `CLOUD_PROVIDER` e sua ausência de implementação deve produzir erro de configuração claro na inicialização, não erro em tempo de requisição. |

#### 3.4.5 Portabilidade

| ID | Requisito |
|---|---|
| **RNF-POR-01** | A API deve poder ser executada em contêiner (Docker) e fornecer `Dockerfile` (e `docker-compose` opcional, com Ollama) para desenvolvimento. |
| **RNF-POR-02** | Toda configuração deve vir de variáveis de ambiente (12-factor app). |
| **RNF-POR-03** | A imagem Docker deve declarar `HEALTHCHECK` apontando para `/health` e executar um único processo (`UVICORN_WORKERS=1`) enquanto os contadores forem em memória. |

#### 3.4.6 Observabilidade

| ID | Requisito |
|---|---|
| **RNF-OBS-01** | Logs estruturados (JSON) com nível, data/hora, `request_id`, rota, status e latência. |
| **RNF-OBS-02** | Logs não devem conter senhas, tokens, chaves de API nem o conteúdo das mensagens, **em nenhum nível de log, inclusive depuração**. Cabeçalhos `Authorization` e `X-Api-Key` devem ser mascarados antes de qualquer registro. |
| **RNF-OBS-03** | Registro em **log** de métricas por requisição de chat: papel (`admin`/`guest`), provedor, modelo, duração e, quando disponível, quantidade de tokens. |
| **RNF-OBS-04** | Eventos de segurança (login do admin com falha, bloqueio por força bruta, limite de uso atingido) devem ser registrados em log, já que não há trilha de auditoria persistente. |
| **RNF-OBS-05** | O `X-Request-ID` deve ser registrado em todas as linhas de log da requisição e devolvido no corpo dos erros; o mesmo identificador deve ser propagado ao provedor de IA quando o protocolo permitir. |

#### 3.4.7 Usabilidade da API

| ID | Requisito |
|---|---|
| **RNF-USA-01** | Respostas de erro padronizadas e legíveis (ver 3.8). |
| **RNF-USA-02** | Nomes de recursos e campos consistentes, em inglês e `snake_case`. |
| **RNF-USA-03** | Documentação OpenAPI com descrição e exemplos para todos os endpoints. |
| **RNF-USA-04** | Erros de limite de uso devem trazer informação suficiente para o frontend exibir mensagem clara (motivo, `Retry-After` ou `resets_at`). |
| **RNF-USA-05** | Nomes de campos, rotas e códigos de erro em inglês; mensagens de `title`/`detail` em pt-BR nesta versão (a API não é multi-idioma). |
| **RNF-USA-06** | O endpoint de chat com `stream = true` e `stream = false` deve ser documentado no OpenAPI com os dois tipos de resposta (`text/event-stream` e `application/json`), apesar de compartilharem a mesma rota. |

---

### 3.5 Regras de negócio

| ID | Regra |
|---|---|
| **RN-01** | Existe **um único usuário salvo**: o administrador, definido na configuração. Não há cadastro público. |
| **RN-02** | Qualquer visitante pode usar o chat como **guest**, sem cadastro, dentro dos limites de uso. |
| **RN-03** | O administrador não tem cota diária de mensagens, mas está sujeito ao limite técnico por minuto e aos limites de tamanho do seu papel. |
| **RN-04** | Alterar o administrador (usuário ou senha) exige alterar a configuração e reiniciar a API. Tokens já emitidos do admin continuam válidos até expirar; para invalidá-los de imediato, troca-se o `JWT_SECRET` (o que também invalida os tokens de convidado). |
| **RN-05** | O chat é efêmero: a API não guarda histórico. O cliente é responsável por manter e reenviar o contexto desejado. |
| **RN-06** | Mensagens com `role = system` enviadas pelo cliente são aceitas somente se `ALLOW_CLIENT_SYSTEM_MESSAGES = true` (e nunca para guest); caso contrário, são rejeitadas com `422`. Se aceitas, devem vir antes da primeira mensagem `user`; o `DEFAULT_SYSTEM_PROMPT` do servidor, se definido, é prefixado ao contexto. Não existe parâmetro separado `system_prompt` (RF-CHAT-09). |
| **RN-07** | O modelo de IA não possui memória; o contexto que o cliente envia é encaminhado integralmente ao provedor (após validação e limites). |
| **RN-08** | A cota do guest é medida por `gid` **e** por IP; a cota efetiva é o menor valor restante. Pedir um novo token de convidado renova a cota do **novo `gid`**, mas **não** a do IP; por isso o limite por IP é o que efetivamente restringe o abuso (RF-USO-04, RF-USO-12). |
| **RN-09** | A cota diária reinicia à 00:00 UTC (janela fixa); `resets_at` é retornado em ISO 8601 UTC e `X-RateLimit-Reset` em *epoch seconds*. O limite por minuto usa janela deslizante de 60 segundos. |
| **RN-10** | Mensagem barrada por limite de uso não consome cota nem chega ao provedor de IA. Falha do provedor antes do primeiro trecho de resposta devolve a cota do `gid` e do IP (RF-USO-05); considera-se primeiro trecho o primeiro evento `data:` com conteúdo não vazio. |
| **RN-11** | Guest não acessa nenhum endpoint `/admin/*`. |
| **RN-12** | Os contadores de uso são voláteis: reiniciar a API (ou o Redis) zera os limites. Essa fragilidade é aceita nesta versão e deixa de existir quando os limites passarem a ser persistidos (seção 5.5). |
| **RN-13** | O guest só pode ter o modelo e o prompt de sistema definidos pelo servidor; não escolhe modelo nem parâmetros de geração (inclusive `max_tokens`). |
| **RN-14** | Uma requisição de chat consome 1 unidade de cota, independentemente do número de mensagens do contexto (RF-USO-11). |
| **RN-15** | Quando as duas cotas forem restritivas, o código de erro prioriza `guest_daily_limit_reached`; se apenas a do IP estiver esgotada, `ip_daily_limit_reached` (RF-USO-12). |
| **RN-16** | "Guest" é o termo canônico em código (`role`, `GUEST_*`); "ghost" e "visitante" são apenas sinônimos de documentação/interface. |

---

### 3.6 Restrições de projeto

| ID | Restrição |
|---|---|
| **RP-01** | Linguagem Python; framework FastAPI. |
| **RP-02** | **Sem banco de dados, ORM ou migrações** nesta versão. |
| **RP-03** | Autenticação baseada em JWT (`PyJWT` ou `python-jose`), com suporte nativo OAuth2/Bearer do FastAPI. |
| **RP-04** | Comunicação com Ollama via `httpx` ou biblioteca `ollama`. |
| **RP-05** | Streaming de respostas via SSE com `StreamingResponse`, incluindo os cabeçalhos de cache/proxy de 3.1.1 e heartbeat periódico. |
| **RP-06** | Padrão Strategy obrigatório para provedores de IA (RNF-MAN-01). A troca de provedor deve ocorrer **apenas** pela variável `AI_PROVIDER` (e, no caso de nuvem, `CLOUD_PROVIDER`/`CLOUD_MODEL`). |
| **RP-07** | Contadores de limite de uso e de tentativas de login ficam em memória (nó único **e um único processo**, `UVICORN_WORKERS=1`) ou em Redis (vários nós/processos). Com mais de um processo ou nó e sem Redis, os limites deixam de ser confiáveis; a API deve recusar essa combinação na inicialização (RNF-CON-06). |
| **RP-08** | Nenhum e-mail é enviado nesta versão (sem SMTP). |
| **RP-09** | O frontend consome o SSE via `fetch`/`ReadableStream`; `EventSource` não é compatível com `POST` + `Authorization` e não deve ser usado (3.1.1). |

---

### 3.7 Catálogo de endpoints

Prefixo comum: `/api/v1`. **Auth:** 🔓 público · 🔐 guest ou admin · 🛡️ somente admin.

#### 3.7.1 Autenticação

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| POST | `/auth/login` | 🔓 | Login do admin; retorna o access token | RF-AUT-02 a 04, 06 |
| POST | `/auth/guest` | 🔓 | Emite token de convidado (guest) | RF-AUT-05, 06, 08 |

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
  "role": "admin"
}
```

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
    "resets_at": "2026-10-29T00:00:00Z"
  }
}
```

O `usage` devolvido na emissão refere-se ao **`gid` recém-criado**; quando a cota do IP já for mais restritiva, `daily_remaining` reflete a cota efetiva (menor valor entre as duas, RF-USO-12). Excesso de emissão por IP: `429` com `guest_token_limit_reached` e `Retry-After`.

#### 3.7.2 Uso

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/usage` | 🔐 | Limites e uso restante do chamador | RF-USO-07 |

**Resposta (`200 OK`, guest)**

```json
{
  "role": "guest",
  "daily_limit": 10,
  "daily_remaining": 7,
  "per_minute_limit": 3,
  "per_minute_remaining": 3,
  "resets_at": "2026-10-29T00:00:00Z"
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

Este é o schema único do objeto `usage`, reutilizado em `/auth/guest`, `/usage`, na resposta JSON do chat e no evento `done` (RD-08).

#### 3.7.3 Chat

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| POST | `/chat` | 🔐 | Envia mensagens e recebe a resposta (JSON ou SSE) | RF-CHAT-01 a 12, RF-USO-01 a 12 |

**Corpo da requisição**

```json
{
  "messages": [
    {"role": "user", "content": "Olá!"},
    {"role": "assistant", "content": "Olá! Como posso ajudar?"},
    {"role": "user", "content": "Explique o que é SSE."}
  ],
  "stream": true
}
```

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `messages` | array | Sim | Lista completa do contexto; a última deve ser `role = user` |
| `stream` | booleano | Não | `true` (padrão): SSE; `false`: JSON completo |

Campos desconhecidos são rejeitados com `422` (`extra="forbid"`). Não há `system_prompt` nem parâmetros de geração no corpo (RF-CHAT-09). Uma requisição consome 1 unidade de cota (RF-USO-11).

**Resposta com `stream = false` (`200 OK`)**

```json
{
  "message": {
    "role": "assistant",
    "content": "SSE é ..."
  },
  "provider": "ollama",
  "model": "llama3.1",
  "finish_reason": "stop",
  "usage": {
    "daily_limit": 10,
    "daily_remaining": 6,
    "per_minute_limit": 3,
    "per_minute_remaining": 2,
    "resets_at": "2026-10-29T00:00:00Z"
  }
}
```

Para o admin, `daily_limit`, `daily_remaining` e `resets_at` são `null`. `finish_reason` ∈ {`stop`, `length`, `timeout`} (RF-CHAT-12).

**Resposta com `stream = true` (`200 OK`, `text/event-stream`)**

Cabeçalhos obrigatórios: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no` e os `X-RateLimit-*` de RF-USO-08. A API envia `: ping` a cada 15 s de inatividade. Cada pedaço é enviado como JSON dentro de `data:`, o que evita quebras de protocolo quando o texto contém novas linhas.

```
: ping

data: {"content": "SSE é "}

data: {"content": "um mecanismo ..."}

event: done
data: {"status": "complete", "finish_reason": "stop", "usage": {"daily_limit": 10, "daily_remaining": 6, "per_minute_limit": 3, "per_minute_remaining": 2, "resets_at": "2026-10-29T00:00:00Z"}}

```

Em caso de falha após o início do fluxo (o HTTP já foi `200`; o erro é sinalizado no evento):

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
  "resets_at": "2026-10-29T00:00:00Z",
  "request_id": "b1f4c7e2-..."
}
```

Cabeçalhos do `429`: `Retry-After` (segundos, para o limite por minuto ou bloqueio de login) e os `X-RateLimit-*` de RF-USO-08. Quando a cota por IP for a restritiva, `code` é `ip_daily_limit_reached` e `detail` cita o limite do local/rede (RN-15).

#### 3.7.4 Administração

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/admin/stats` | 🛡️ | Estatísticas de uso em memória (desde a última inicialização) | RF-ADM-02 |
| GET | `/admin/ai/status` | 🛡️ | Status do provedor de IA | RF-ADM-03 |
| GET | `/admin/guests` | 🛡️ | Lista guests ativos e uso por IP (para obter o `gid`) | RF-ADM-05 |
| POST | `/admin/usage/reset` | 🛡️ | Zera a cota de um guest (`gid`) ou IP (`ip`) | RF-ADM-04, RF-USO-10 |

**`GET /admin/stats` (`200 OK`)**

```json
{
  "since": "2026-10-28T10:00:00Z",
  "chat_requests_total": 128,
  "guest_distinct": 14,
  "rate_limited_total": 7,
  "provider_errors_total": 2,
  "avg_response_seconds": 3.42
}
```

**`GET /admin/ai/status` (`200 OK`)** — nunca expõe chaves:

```json
{
  "provider": "ollama",
  "model": "llama3.1",
  "available": true,
  "checked_at": "2026-10-28T14:30:00Z"
}
```

**`GET /admin/guests` (`200 OK`)**

```json
{
  "guests": [
    {
      "gid": "6f1c9a3e-1b2d-4c5f-8a90-0123456789ab",
      "ip": "203.0.113.10",
      "daily_used": 4,
      "daily_limit": 10,
      "last_seen_at": "2026-10-28T14:25:00Z"
    }
  ],
  "ip_usage": [
    { "ip": "203.0.113.10", "daily_used": 12, "daily_limit": 30 }
  ]
}
```

**`POST /admin/usage/reset`** — corpo e resposta:

```json
{ "scope": "gid", "value": "6f1c9a3e-1b2d-4c5f-8a90-0123456789ab" }
```

`scope` ∈ {`gid`, `ip`}; resposta `204 No Content`. Alvo inexistente retorna `404` (`not_found`).

#### 3.7.5 Sistema

| Método | Rota | Auth | Descrição | Requisitos |
|---|---|---|---|---|
| GET | `/health` | 🔓 | Saúde da API | RF-SIS-01 |
| GET | `/health/ready` | 🔓 | Prontidão (inclui provedor de IA) | RF-SIS-04 |
| GET | `/docs`, `/redoc` | 🔓 | Documentação OpenAPI | RF-SIS-02 |

**`GET /health` (`200 OK`)**

```json
{ "status": "ok", "version": "1.1.0", "request_id": "b1f4c7e2-..." }
```

**`GET /health/ready`** — `200` quando o provedor responde; `503` com `{"status": "unavailable", "provider": "ollama"}` quando não responde.

Todas as respostas trazem `X-Request-ID` (RF-SIS-03). `/health` e `/docs` ficam fora do prefixo `/api/v1`.

---

### 3.8 Padrão de erros

Os erros seguem uma estrutura inspirada na RFC 9457 (Problem Details):

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

Para erros de validação (`422`), incluir o campo `errors` com a lista de campos inválidos e motivos:

```json
{
  "type": "about:blank",
  "title": "Requisição inválida",
  "status": 422,
  "code": "validation_error",
  "detail": "Um ou mais campos são inválidos.",
  "errors": [
    { "field": "messages.2.role", "reason": "deve ser system, user ou assistant" }
  ],
  "request_id": "b1f4c7e2-..."
}
```

Esse formato deve substituir o `422` padrão do FastAPI (RNF-USA-01). Respostas `401` incluem `WWW-Authenticate: Bearer` (RF-AUT-10).

Erros ocorridos **depois** de iniciado o SSE não podem mudar o status HTTP (já `200`); nesse caso, o erro vai no evento `error` com `code` e `request_id` (3.7.3), e a métrica de erro do provedor é registrada normalmente (RF-ADM-02).

| HTTP | Quando ocorre | Exemplo de `code` |
|---|---|---|
| 400 | Requisição malformada | `bad_request` |
| 401 | Token ausente/inválido/expirado; credenciais inválidas | `invalid_credentials`, `token_expired`, `invalid_token` |
| 403 | Sem permissão (guest em rota de admin) | `forbidden` |
| 404 | Recurso inexistente (inclusive alvo de reset) | `not_found` |
| 413 | Corpo acima de `MAX_REQUEST_BODY_BYTES` | `payload_too_large` |
| 422 | Falha de validação (inclui mensagem `system` não permitida e contexto acima do limite) | `validation_error` |
| 429 | Limite de uso, de emissão de tokens ou de tentativas de login excedido | `rate_limited`, `guest_daily_limit_reached`, `ip_daily_limit_reached`, `guest_token_limit_reached`, `login_locked` |
| 500 | Erro interno | `internal_error` |
| 502 | Erro retornado pelo provedor de IA | `ai_provider_error` |
| 503 | Prontidão indisponível (`/health/ready`) | `provider_unavailable` |
| 504 | Timeout do provedor de IA | `ai_provider_timeout` |

---

## 4. Critérios de aceitação

A API será considerada aceita quando:

| ID | Critério |
|---|---|
| **CA-01** | A API inicia com `ADMIN_USERNAME` e `ADMIN_PASSWORD_HASH` válidos e **não inicia** se qualquer um estiver ausente. |
| **CA-02** | O admin faz login com usuário e senha corretos e recebe token; com senha incorreta recebe `401` genérico. |
| **CA-03** | Um visitante obtém token em `POST /auth/guest` e usa o chat com ele, sem cadastro. |
| **CA-04** | `POST /chat` com `stream = true` entrega a resposta incrementalmente e finaliza com o evento `done`. |
| **CA-05** | `POST /chat` com `stream = false` retorna a resposta completa em JSON. |
| **CA-06** | Nenhuma mensagem é persistida: não existe banco, e o conteúdo das mensagens não aparece em log em **nenhum nível, inclusive depuração**. |
| **CA-07** | Ao esgotar a cota diária, o guest recebe `429` (`guest_daily_limit_reached`) com `resets_at`, e a requisição não chega ao provedor de IA. |
| **CA-08** | Ao exceder o limite por minuto, o guest recebe `429` com `Retry-After`. |
| **CA-09** | Pedir novo token cria um novo `gid` (cota do `gid` renovada), mas a cota por IP continua valendo; a cota efetiva é o menor valor entre as duas. |
| **CA-10** | A emissão de tokens de convidado é limitada por IP e retorna `429` (`guest_token_limit_reached`) com `Retry-After` ao exceder. |
| **CA-11** | `GET /usage` mostra ao guest o uso restante (diário e por minuto), coerente com os cabeçalhos `X-RateLimit-*` do chat. |
| **CA-12** | O admin envia mensagens sem cota diária; guest recebe `403` em qualquer `/admin/*`. |
| **CA-13** | Falha do provedor de IA antes do primeiro trecho devolve a cota do `gid` **e** do IP. |
| **CA-14** | Com o Ollama parado, `POST /chat` retorna `502`/`504` (ou evento `error`) e os demais endpoints continuam funcionando. |
| **CA-15** | Trocar o provedor por configuração (`AI_PROVIDER`, `CLOUD_PROVIDER`) não exige alteração nas rotas, validado com um provedor *fake* registrado na factory; o `CloudProvider` real está fora do escopo (§5.1). |
| **CA-16** | O login do admin é bloqueado temporariamente após `LOGIN_MAX_ATTEMPTS` falhas. |
| **CA-17** | A documentação OpenAPI lista **todos** os endpoints (incluindo `/admin/*` e `/health*`) com exemplos, e documenta os dois tipos de resposta do chat. |
| **CA-18** | Cobertura de testes automatizados ≥ 80% e pipeline de lint/tipagem sem falhas. |
| **CA-19** | No navegador, o frontend lê `X-RateLimit-*`, `Retry-After` e `X-Request-ID` (CORS com `expose_headers`). |
| **CA-20** | O admin lista os guests (`/admin/guests`), obtém um `gid` e zera sua cota (`204`); alvo inexistente retorna `404`. |
| **CA-21** | A resposta é interrompida ao atingir `AI_MAX_OUTPUT_TOKENS` ou `AI_MAX_OUTPUT_SECONDS`, com `finish_reason` adequado, sem erro. |
| **CA-22** | `401` traz `WWW-Authenticate: Bearer`; `422` segue o formato de 3.8 com `errors[]`. |
| **CA-23** | A API recusa-se a iniciar com `UVICORN_WORKERS > 1` sem `REDIS_URL`. |

---

## 5. Apêndices

### 5.1 Itens fora do escopo (versão 1.1)

- Frontend React e seus requisitos.
- **Banco de dados e qualquer persistência** (próxima versão).
- Cadastro público de usuários, perfil, troca de senha, múltiplos usuários e gestão de usuários pelo admin.
- Histórico de conversas, mensagens salvas, fixar/arquivar, ramificação (editar/regenerar) e feedback de respostas.
- Verificação de e-mail, recuperação de senha e envio de e-mail (SMTP).
- Refresh token, sessões por dispositivo e revogação de tokens no servidor.
- Auditoria persistente, estatísticas históricas e exportação de dados (LGPD).
- Cobrança, planos pagos e controle de custo por tokens.
- Login social, autenticação multifator, upload de arquivos e RAG.
- Implementação efetiva do `CloudProvider` (somente o contrato e o esqueleto estão no escopo).

### 5.2 Decisões e premissas adotadas neste SRS

| # | Decisão | Motivo |
|---|---|---|
| 1 | Sem banco de dados nesta versão; persistência na próxima versão da API. | Definido pelo solicitante. |
| 2 | Único usuário salvo: o admin, via variáveis de ambiente (`ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`). | Definido pelo solicitante. A senha fica como **hash** na configuração, nunca em texto puro. |
| 3 | O guest é identificado por um **token de convidado** (JWT, `gid`) e também por **IP**. | Só o token seria fácil de burlar (basta pedir outro); o IP fecha essa brecha. |
| 4 | Limites padrão do guest: 10 mensagens/dia, 3/min, contexto de até 20 mensagens de 2000 caracteres. | Valores iniciais configuráveis, a validar com testes reais, sobretudo com IA local. |
| 5 | O chat recebe a **lista completa de mensagens** do cliente e não persiste nada. | Mantém a decisão do levantamento e é a única opção sem banco. |
| 6 | Sem refresh token: token do admin curto (60 min) e do guest de 24 h. | Sem armazenamento, não há como revogar ou rotacionar tokens. |
| 7 | Cota diária por janela fixa, reiniciando à 00:00 UTC. | Simples e previsível; a janela deslizante diária exigiria guardar cada requisição. |
| 8 | Contagem da cota ao **iniciar** a geração, com devolução se o provedor falhar antes do primeiro trecho. | Evita que o guest abuse de streams longos e não o penaliza por falha do servidor. |
| 9 | Mensagens `system` do cliente nunca são aceitas de guest. | Impede que um anônimo reescreva o comportamento do modelo. |
| 10 | Contadores em memória (nó único e processo único), com Redis opcional. | Mantém a API sem banco; Redis só é necessário com vários nós/processos (RD-03, RNF-CON-06). |
| 11 | Autenticação e armazenamento de contadores isolados atrás de módulos/interfaces. | Facilita a troca por banco na próxima versão sem reescrever as rotas. |
| 12 | Streaming consumido por `fetch`/`ReadableStream`, não `EventSource`. | `EventSource` não suporta `POST` nem `Authorization`; a decisão evita um contrato impossível de implementar no frontend (3.1.1, RP-09). |
| 13 | Limite de saída por `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS`, decidido pelo servidor. | Sem isso, uma resposta poderia durar indefinidamente, com custo imprevisível na nuvem e ocupação do modelo local (RF-CHAT-12). |
| 14 | Um único parâmetro de orientação do sistema (mensagens `system`); sem `system_prompt`. | Evita dois mecanismos concorrentes para a mesma função (RF-CHAT-09, RN-06). |
| 15 | Reset de cota por `gid` acompanhado de `GET /admin/guests`. | Sem a listagem, o `gid` não seria obtenível pelo admin e o reset seria inoperável (RF-ADM-05). |
| 16 | Cota efetiva = menor valor entre `gid` e IP, com código de erro distinto. | Torna explícito o comportamento quando as duas cotas concorrem (RF-USO-12, RN-15). |

### 5.3 Pontos em aberto

| # | Questão | Impacto |
|---|---|---|
| 1 | Qual o modelo e o hardware finais da IA local? | Define metas realistas de latência e concorrência (RNF-DES-02/04) e os limites do guest. |
| 2 | Os valores de cota do guest (10/dia, 3/min) atendem ao uso esperado? | Ajuste de `GUEST_*`. |
| 3 | O limite por IP prejudicará usuários atrás de NAT compartilhado (redes de empresa, universidade)? | Pode exigir um limite por IP mais alto ou outra forma de identificar o guest. |
| 4 | Qual o valor de `AI_MAX_OUTPUT_TOKENS` por papel (guest x admin)? | Define custo e duração máxima de resposta; o parâmetro já existe (RF-CHAT-12), mas o valor depende do modelo. |
| 5 | Haverá mais de um nó da API antes da chegada do banco? | Se sim, o Redis passa a ser obrigatório (RP-07, RNF-CON-06). |
| 6 | Mensagens `system` do admin serão permitidas? | Define o valor de `ALLOW_CLIENT_SYSTEM_MESSAGES` (padrão: não). |
| 7 | Haverá proteção anti-bot na emissão de token (captcha, *proof of work*)? | Hoje a defesa é apenas a cota por IP; fazenda de tokens com muitos IPs continua possível (risco aceito). |
| 8 | Qual o prazo máximo do *graceful shutdown* com streams abertos? | Evita que o desligamento trave esperando `AI_MAX_OUTPUT_SECONDS` (RNF-CON-04). |

### 5.4 Resumo de rastreabilidade (módulo → endpoints)

| Módulo | Requisitos | Endpoints |
|---|---|---|
| Autenticação | RF-AUT-01 a 10 | `/auth/login`, `/auth/guest` |
| Chat | RF-CHAT-01 a 12 | `/chat` |
| Limites de uso | RF-USO-01 a 12 | `/usage`, `/chat` (cabeçalhos e `429`) |
| Administração | RF-ADM-01 a 05 | `/admin/*` |
| Sistema | RF-SIS-01 a 04 | `/health*`, `/docs`, `/redoc` |

### 5.5 Preparação para a próxima versão (com banco de dados)

O que volta quando o banco entrar, e como esta versão já deixa o caminho livre:

| Item que volta | Como esta versão se prepara |
|---|---|
| Banco `ai_chat` (MySQL), ORM e migrações | `core/auth.py` e `UsageStore` isolados (RNF-MAN-04 e 05); rotas não dependem da origem do usuário. |
| Cadastro, perfil e múltiplos usuários | O admin da configuração vira o primeiro registro (seed) na tabela de usuários. |
| Histórico, conversas, fixar/arquivar, feedback | `POST /chat` já recebe o contexto completo e devolve `message`; basta acrescentar `conversation_id` e gravar. |
| Refresh token e revogação de sessões | O JWT já carrega `role` e `sub`; entra o `sid` e a tabela de sessões. |
| Persistência dos limites do guest | `UsageStore` ganha implementação em banco, e os contadores deixam de zerar ao reiniciar (RN-12). |
| E-mail (verificação e recuperação de senha) | Sem impacto nas rotas atuais. |
| Auditoria e estatísticas históricas | As estatísticas em memória (RF-ADM-02) passam a ser calculadas a partir do banco. |
