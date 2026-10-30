# Compatibilidade com APIs externas — OpenAI, Anthropic e Ollama

> Documento de apoio ao [SRS v2.0](srs-api-chat-ia-v1.md) e ao
> [plano de implementação v2.0](plano-implementacao-api-chat-ia-v1.md).
> **Estado:** OpenAI implementado na F14, Anthropic/Claude Code na F15 e Ollama
> na F16. A API em produção permanece na v1.1.0.

## 1. Objetivo

A NEXA API expõe, além da API nativa, **três camadas de compatibilidade** para que
sistemas existentes a usem como se fosse o provedor original:

| Superfície | Prefixo | Clientes típicos |
|---|---|---|
| OpenAI | `/v1` | Cursor, LangChain, n8n, SDKs OpenAI |
| Anthropic | `/v1` | **Claude Code**, SDKs Anthropic |
| Ollama | `/api` | clientes Ollama, ferramentas locais |

As camadas são **traduções stateless**: não persistem conversa, não duplicam
regras de negócio e compartilham cotas, rate limit, allowlist, timeouts e
roteamento do núcleo.

## 2. Autenticação

### 2.1 API key NEXA

Sistemas externos usam uma chave `nxa_...` configurada em `COMPAT_API_KEYS`
(lista separada por vírgula):

```env
COMPAT_API_KEYS=nxa_live_7f3c9a2e...,nxa_test_1b2d4c5f...
COMPAT_API_KEY_RATE_PER_MIN=60
```

Formas aceitas:

| Superfície | Header |
|---|---|
| OpenAI | `Authorization: Bearer nxa_...` |
| Anthropic | `x-api-key: nxa_...` ou `Authorization: Bearer nxa_...` |
| Ollama | `Authorization: Bearer nxa_...` |

Regras:

- Comparação em tempo constante (`hmac.compare_digest`).
- Log apenas do prefixo (8 caracteres) e fingerprint; nunca a chave completa.
- Chave ausente/inválida retorna `401` no envelope da superfície.
- Cada chave tem limite próprio por minuto (`COMPAT_API_KEY_RATE_PER_MIN`).
- JWT de admin/usuário também é aceito nas rotas de compatibilidade, quando
  enviado em `Authorization: Bearer`.

### 2.2 Modo anônimo (somente desenvolvimento)

```env
COMPAT_ALLOW_ANONYMOUS=false
COMPAT_OLLAMA_ALLOW_LOCALHOST=false
```

- `COMPAT_ALLOW_ANONYMOUS=true` libera as rotas de compatibilidade sem chave.
- `COMPAT_OLLAMA_ALLOW_LOCALHOST=true` libera apenas conexões de loopback para
  clientes Ollama. O IP é o da conexão direta (não considera `X-Forwarded-For`).
- Em `ENVIRONMENT=production`, a API **recusa iniciar** com qualquer um dos dois
  habilitado.

## 3. Compatibilidade OpenAI

### 3.1 Endpoints

| Método | Rota | Descrição |
|---|---|---|
| POST | `/v1/chat/completions` | Chat completo ou streaming |
| GET | `/v1/models` | Lista de modelos |

### 3.2 Requisição

```json
{
  "model": "openai/gpt-4o-mini",
  "messages": [
    {"role": "system", "content": "Seja breve."},
    {"role": "user", "content": "Olá!"}
  ],
  "stream": false,
  "temperature": 0.7,
  "top_p": 1.0,
  "max_tokens": 256,
  "stop": ["\n\n"],
  "tools": [],
  "tool_choice": "auto",
  "response_format": {"type": "text"},
  "user": "usuario-123"
}
```

### 3.3 Campos suportados

| Campo | Suporte | Observação |
|---|---|---|
| `model` | ✅ | Model ID, alias ou nome do provedor |
| `messages` | ✅ | `system`, `user`, `assistant`, `tool` |
| `stream` | ✅ | SSE com `data: [DONE]` |
| `stream_options` | ⚠️ | `include_usage` suportado; demais ignorados |
| `temperature` | ⚠️ | Aceito e validado (0–2); o adapter atual não repassa o valor |
| `top_p` | ⚠️ | Aceito e validado (0–1); o adapter atual não repassa o valor |
| `max_tokens` / `max_completion_tokens` | ✅ | Limitado por `AI_MAX_OUTPUT_TOKENS` |
| `stop` | ⚠️ | Até 4 sequências; validado e ainda não repassado ao adapter |
| `tools` / `tool_choice` | ✅ | Requer capacidade `tools` |
| `response_format` | ⚠️ | `text` e `json_object`; `json_schema` é tratado como `json_mode` |
| `user` | ⚠️ | Usado apenas para logging/rate limit; não persiste |
| `n` | ❌ | Retorna `400 unsupported_parameter` se `n > 1` |
| `presence_penalty`, `frequency_penalty`, `seed`, `logit_bias` | ⚠️ | Aceitos e ignorados quando o provedor não suporta |
| `logprobs`, `top_logprobs` | ❌ | `400 unsupported_parameter` |

### 3.4 Resposta (`stream=false`)

```json
{
  "id": "chatcmpl-nexa-0192f3a4",
  "object": "chat.completion",
  "created": 1761748200,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Olá! Como posso ajudar?",
        "tool_calls": []
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 8,
    "total_tokens": 20
  }
}
```

### 3.5 Streaming

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1761748200,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1761748200,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"Olá"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1761748200,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

- O último chunk com conteúdo traz `finish_reason`.
- Se `stream_options.include_usage=true`, um chunk final com `usage` é enviado
  antes de `[DONE]`.
- Erros após o início vão como `data: {"error": {...}}` e encerram o stream.

### 3.6 `GET /v1/models`

```json
{
  "object": "list",
  "data": [
    {"id": "openai/gpt-4o-mini", "object": "model", "created": 1761748200, "owned_by": "nexa"},
    {"id": "anthropic/claude-sonnet-4-5", "object": "model", "created": 1761748200, "owned_by": "nexa"}
  ]
}
```

### 3.7 Erros

```json
{
  "error": {
    "message": "O modelo informado não existe.",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found"
  }
}
```

| HTTP | `type` | Exemplos de `code` |
|---|---|---|
| 400 | `invalid_request_error` | `unsupported_parameter`, `invalid_value` |
| 401 | `authentication_error` | `invalid_api_key` |
| 403 | `permission_error` | `forbidden` |
| 404 | `invalid_request_error` | `model_not_found` |
| 422 | `invalid_request_error` | `validation_error`, `unsupported_capability` |
| 429 | `rate_limit_error` | `rate_limited`, `quota_exceeded` |
| 502/503/504 | `api_error` | `provider_error`, `provider_unavailable`, `provider_timeout` |

## 4. Compatibilidade Anthropic (Claude Code)

### 4.1 Endpoints

| Método | Rota | Descrição |
|---|---|---|
| POST | `/v1/messages` | Messages API (JSON ou SSE) |
| POST | `/v1/messages/count_tokens` | Contagem de tokens de entrada |

### 4.2 Headers

| Header | Obrigatório | Valor |
|---|---|---|
| `x-api-key` | ✅ (ou Bearer) | `nxa_...` |
| `anthropic-version` | ✅ | `2023-06-01` (ou superior suportado) |
| `content-type` | ✅ | `application/json` |
| `anthropic-beta` | ❌ | Aceito e ignorado quando não aplicável |

### 4.3 Requisição

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
      "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
      }
    }
  ],
  "tool_choice": {"type": "auto"},
  "temperature": 0.7,
  "top_p": 1.0,
  "top_k": 40,
  "stop_sequences": ["\n\nHuman:"],
  "metadata": {"user_id": "usuario-123"},
  "stream": true
}
```

### 4.4 Blocos de conteúdo

`messages[].content` e `system` aceitam string ou lista de blocos:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "O que há nesta imagem?"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
  ]
}
```

Blocos suportados: `text`, `image`, `tool_use` (assistant) e `tool_result`
(user). Blocos desconhecidos retornam `400 invalid_request_error`.

### 4.5 Resposta (`stream=false`)

```json
{
  "id": "msg_nexa_0192f3a4",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-5",
  "content": [
    {"type": "text", "text": "Vou listar os arquivos."},
    {"type": "tool_use", "id": "toolu_0192f3a4", "name": "list_files", "input": {"path": "."}}
  ],
  "stop_reason": "tool_use",
  "stop_sequence": null,
  "usage": {"input_tokens": 42, "output_tokens": 31}
}
```

`stop_reason` ∈ {`end_turn`, `max_tokens`, `stop_sequence`, `tool_use`}.

### 4.6 Streaming

Sequência de eventos:

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_nexa_...","type":"message","role":"assistant","model":"claude-sonnet-4-5","content":[],"stop_reason":null,"usage":{"input_tokens":42,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Vou "}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":31}}

event: message_stop
data: {"type":"message_stop"}
```

Para ferramentas, o bloco usa `type: "tool_use"` e os deltas usam
`input_json_delta` com `partial_json`. Eventos `ping` podem ser enviados a
qualquer momento; `error` encerra o stream.

### 4.7 `count_tokens`

```json
{"input_tokens": 42}
```

- Usa a contagem do provedor quando disponível.
- Caso contrário, usa a heurística documentada de **~4 caracteres por token**
  (`ceil(caracteres / 4)`, mínimo 1), com margem de erro; `tools` também entram
  na estimativa.
- Não consome cota (o limite por minuto da API key continua valendo).

### 4.8 Erros

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "O campo max_tokens é obrigatório."
  }
}
```

| HTTP | `type` |
|---|---|
| 400 | `invalid_request_error` |
| 401 | `authentication_error` |
| 403 | `permission_error` |
| 404 | `not_found_error` |
| 422 | `invalid_request_error` |
| 429 | `rate_limit_error` |
| 502/503/504 | `api_error` |

### 4.9 Notas para Claude Code

- Configuração típica no cliente:

  ```bash
  export ANTHROPIC_BASE_URL=http://localhost:8000
  export ANTHROPIC_AUTH_TOKEN=nxa_...
  export ANTHROPIC_API_KEY=nxa_...
  ```

- O Claude Code exige `POST /v1/messages` com streaming, `tools` e
  `count_tokens`; a API cobre os três.
- `system` pode vir como string ou lista de blocos; ambos são aceitos.
- `tool_use`/`tool_result` são repassados ao provedor; a execução da ferramenta
  é responsabilidade do cliente.
- Campos/headers não suportados são ignorados de forma documentada, nunca
  resultam em erro genérico.
- O teste E2E da fase F15 valida uma tarefa real com ferramenta contra o
  provedor fake.

## 5. Compatibilidade Ollama

### 5.1 Endpoints

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/chat` | Chat (NDJSON) |
| GET | `/api/tags` | Modelos disponíveis |
| POST | `/api/show` | Detalhes de um modelo |
| GET | `/api/ps` | Modelos em execução (agregado/opcional) |

### 5.2 `POST /api/chat`

```json
{
  "model": "ollama/llama3.1",
  "messages": [{"role": "user", "content": "Olá!"}],
  "stream": true,
  "tools": [],
  "options": {"temperature": 0.7, "num_predict": 256},
  "format": "json",
  "keep_alive": "5m"
}
```

Resposta não-streaming:

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

Streaming (uma linha JSON por trecho):

```
{"model":"llama3.1:latest","created_at":"...","message":{"role":"assistant","content":"Olá"},"done":false}
{"model":"llama3.1:latest","created_at":"...","message":{"role":"assistant","content":"!"},"done":false}
{"model":"llama3.1:latest","created_at":"...","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","prompt_eval_count":12,"eval_count":8}
```

Regras de tradução:

- `stream` é o padrão do contrato Ollama; a rota responde
  `application/x-ndjson` com `done:false` por trecho e uma linha final
  `done:true` (inclui `done_reason`, `prompt_eval_count` e `eval_count`).
- `options.num_predict` é limitado por `AI_MAX_OUTPUT_TOKENS`.
- `options.temperature`, `options.top_p` e `options.stop` são aceitos e
  **ignorados** pelo adapter atual, como nas demais superfícies.
- `format` diferente de `null` exige um modelo com `json_mode`; caso contrário a
  rota responde `400` com `{"error": "..."}`.
- `keep_alive` é aceito por compatibilidade e **ignorado**: o gateway não mantém
  modelos carregados nem estado de conversa.
- `tools[].function.parameters` vira o schema nativo; `tool_calls` retornam com
  `arguments` como objeto JSON (não string), como no Ollama.
- Imagens (`images`) exigem modelo com `vision`; o base64 não é repassado ao
  provedor, apenas o marcador `[imagem]` no conteúdo da mensagem.

### 5.3 `GET /api/tags`

Os nomes publicados são **Model IDs canônicos** (`provider/model`), aceitos de
volta em `/api/chat` e `/api/show`; o sufixo `:latest` do nome do provedor é
preservado quando existir. Consultar o catálogo não consome cota.

```json
{
  "models": [
    {
      "name": "ollama/llama3.1:latest",
      "model": "ollama/llama3.1:latest",
      "modified_at": "2026-10-29T14:30:00Z",
      "size": 4920739055,
      "digest": "sha256:4f2c...",
      "details": {
        "family": "llama",
        "parameter_size": "8.0B",
        "quantization_level": "Q4_K_M"
      }
    }
  ]
}
```

`size` é `0` quando o provedor não informa o tamanho e `digest` é derivado do
Model ID (identificador estável, não o hash do arquivo do modelo).

### 5.4 `POST /api/show`

Aceita o Model ID canônico ou o nome simples do modelo.

```json
{
  "model": "ollama/llama3.1:latest",
  "details": {"family": "llama", "parameter_size": "8.0B", "quantization_level": "Q4_K_M"},
  "capabilities": ["completion", "tools", "vision"],
  "modified_at": "2026-10-29T14:30:00Z"
}
```

### 5.5 `GET /api/ps`

O gateway é stateless e não mantém modelos carregados; a rota responde sempre:

```json
{"models": []}
```

A rota existe para clientes Ollama que a consultam na inicialização.

### 5.6 Erros

```json
{"error": "model 'x' not found"}
```

| HTTP | Situação |
|---|---|
| 400 | Requisição inválida (validação, capacidade ou `format`) |
| 401 | API key ausente/inválida |
| 404 | Modelo inexistente |
| 429 | Cota/rate limit |
| 502/503/504 | Falha do provedor |

Erros de validação nativa (`422`) são remapeados para `400` nesta superfície.

## 6. Tabelas de tradução

### 6.1 Papéis

| Nativo | OpenAI | Anthropic | Ollama |
|---|---|---|---|
| `system` | `system` | `system` (campo separado) | `system` |
| `user` | `user` | `user` | `user` |
| `assistant` | `assistant` | `assistant` | `assistant` |
| `tool` | `tool` | `tool_result` (bloco) | `tool` |

### 6.2 Motivo de término

| Nativo | OpenAI `finish_reason` | Anthropic `stop_reason` | Ollama `done_reason` |
|---|---|---|---|
| `stop` | `stop` | `end_turn` | `stop` |
| `length` | `length` | `max_tokens` | `length` |
| `tool_use` | `tool_calls` | `tool_use` | `tool_calls` |
| `timeout` | `length` | `max_tokens` | `length` |
| `stop_sequence` | `stop` | `stop_sequence` | `stop` |

### 6.3 Tokens

| Nativo | OpenAI | Anthropic | Ollama |
|---|---|---|---|
| `prompt_tokens` | `usage.prompt_tokens` | `usage.input_tokens` | `prompt_eval_count` |
| `completion_tokens` | `usage.completion_tokens` | `usage.output_tokens` | `eval_count` |
| `total_tokens` | `usage.total_tokens` | soma | soma |

### 6.4 Ferramentas

| Nativo | OpenAI | Anthropic | Ollama |
|---|---|---|---|
| `tools[].name` | `function.name` | `name` | `function.name` |
| `tools[].description` | `function.description` | `description` | `function.description` |
| `tools[].parameters` | `function.parameters` | `input_schema` | `function.parameters` |
| `tool_calls[]` | `message.tool_calls[]` | blocos `tool_use` | `message.tool_calls[]` (`arguments` objeto) |
| resultado | mensagem `role=tool` | bloco `tool_result` | mensagem `role=tool` com `tool_name` |

### 6.5 Imagens

| Nativo | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| `image_url` (data URL) | `image_url` | bloco `image` base64 | `inlineData` |

Provedores sem visão retornam `422 unsupported_capability` quando há imagem.

## 7. Cotas e limites

As rotas de compatibilidade aplicam:

- cota diária do guest/usuário (quando aplicável);
- limite por minuto do sujeito;
- limite por minuto da API key;
- allowlist de modelos;
- validação de capacidade;
- `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS`;
- limites de contexto do papel.

Requisições barradas **não** chegam ao provedor e **não** debitam cota.
A cota é debitada uma única vez, mesmo com fallback antes do primeiro token.

## 8. Segurança

- Nenhuma chave de provedor, API key NEXA ou header interno aparece em resposta,
  log ou mensagem de erro.
- O conteúdo da conversa não é registrado; apenas metadados.
- SQL nunca é construído com entrada do usuário.
- O `X-Request-ID` da NEXA é devolvido no header das respostas de compatibilidade.
- Em produção, `COMPAT_ALLOW_ANONYMOUS` e `COMPAT_OLLAMA_ALLOW_LOCALHOST` devem
  ser `false`; a API recusa iniciar caso contrário.

## 9. Matriz de compatibilidade

| Recurso | OpenAI | Anthropic | Ollama |
|---|---|---|---|
| Chat JSON | ✅ | ✅ | ✅ |
| Streaming | ✅ | ✅ | ✅ (NDJSON) |
| Tools | ✅ | ✅ | ✅ |
| Visão | ✅ | ✅ | ✅* |
| JSON mode | ✅ | ⚠️ emulado | ✅* |
| System prompt | ✅ | ✅ | ✅ |
| Múltiplas escolhas (`n`) | ❌ | ❌ | ❌ |
| Logprobs | ❌ | ❌ | ❌ |
| Embeddings | ❌ | ❌ | ❌ |
| Histórico persistido | ❌ | ❌ | ❌ |
| `count_tokens` | ❌ | ✅ | ❌ |

`*` Depende do modelo/provedor.

## 10. Limitações conhecidas

- Não há persistência de conversa em nenhuma superfície.
- Ferramentas são executadas pelo cliente; a API apenas repassa `tool_use`/`tool_calls`.
- Embeddings, fine-tuning, batches e arquivos estão fora do escopo.
- `n > 1` não é suportado.
- `logprobs` não é suportado.
- A contagem de tokens da Anthropic pode ser estimada quando o provedor não a expõe.
- Campos específicos de provedor não mapeados são ignorados ou rejeitados de
  forma documentada, nunca silenciosamente incorreta.

## 11. Exemplos com `curl`

### OpenAI

```bash
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $NEXA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Olá!"}],"stream":false}'
```

### Anthropic

```bash
curl -sS -X POST http://localhost:8000/v1/messages \
  -H "x-api-key: $NEXA_API_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"anthropic/claude-sonnet-4-5","max_tokens":256,"messages":[{"role":"user","content":"Olá!"}]}'
```

### Ollama

```bash
curl -sS -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $NEXA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"ollama/llama3.1","messages":[{"role":"user","content":"Olá!"}],"stream":false}'
```

## 12. Checklist de testes de contrato

- [ ] OpenAI JSON: request mínimo, system, tools, `response_format`.
- [ ] OpenAI streaming: chunks, `finish_reason`, `[DONE]`, `include_usage`.
- [ ] OpenAI `/v1/models`: shape `list`.
- [ ] OpenAI erros: 400/401/404/429/5xx.
- [ ] Anthropic JSON: `system` string/blocos, `content[]`, `stop_reason`.
- [ ] Anthropic streaming: sequência completa de eventos.
- [ ] Anthropic tools: `tool_use`, `input_json_delta`, `tool_result`.
- [ ] Anthropic `count_tokens`.
- [ ] Anthropic erros: envelope `type=error`.
- [x] Ollama chat: NDJSON completo e `done_reason`.
- [x] Ollama tags/show/ps.
- [x] Ollama erros: `{"error": "..."}`.
- [ ] Auth: chave ausente/inválida em cada superfície.
- [ ] Cotas: 429 no envelope correto.
- [ ] Nenhuma chave/conteúdo em log.
