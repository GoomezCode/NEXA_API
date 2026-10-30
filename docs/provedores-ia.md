# Provedores de IA — configuração, roteamento e capacidades

> Documento de apoio ao [SRS v2.0](srs-api-chat-ia-v1.md) e ao
> [plano de implementação v2.0](plano-implementacao-api-chat-ia-v1.md).
> **Estado:** planejado para as fases F12–F17; a API em produção permanece na v1.1.0.

## 1. Objetivo

A NEXA API é um **gateway multi-provedor**: o cliente escolhe um **Model ID** no
formato `provider/model` e a API resolve o adapter, valida as capacidades, aplica
cotas e encaminha a chamada. O cliente não precisa conhecer SDKs, endpoints ou
credenciais de cada provedor.

```
cliente → NEXA API → ProviderRouter → adapter → provedor
                         │
                         ├── aliases / allowlist
                         ├── capacidades
                         ├── fallback (antes do 1º token)
                         └── catálogo agregado
```

## 2. Provedores suportados

| Provedor | ID | Protocolo | Autenticação | Catálogo |
|---|---|---|---|---|
| Ollama local | `ollama` | `POST {OLLAMA_URL}/api/chat` (NDJSON) | opcional (`OLLAMA_API_KEY`) | `GET /api/tags` |
| Ollama Cloud | `ollama_cloud` | `POST {OLLAMA_CLOUD_URL}/api/chat` (NDJSON) | `Authorization: Bearer` | `GET /api/tags` |
| OpenAI | `openai` | `POST {OPENAI_BASE_URL}/chat/completions` (SSE) | `Authorization: Bearer` | `GET /models` |
| Anthropic | `anthropic` | `POST {ANTHROPIC_BASE_URL}/v1/messages` (SSE) | `x-api-key` | `GET /v1/models` |
| Google Gemini | `google` | `POST {GOOGLE_BASE_URL}/v1beta/models/{model}:generateContent` e `:streamGenerateContent?alt=sse` | `x-goog-api-key` | `GET /v1beta/models` |
| OpenAI-compatible | `openai_compatible` | `POST {OPENAI_COMPAT_BASE_URL}/chat/completions` (SSE) | `Authorization: Bearer` | `GET /models` |
| Fake (testes) | `fake` | Em processo | — | Estático |

O provedor `openai_compatible` cobre Groq, OpenRouter, DeepSeek, Azure OpenAI,
vLLM, LM Studio, Together e qualquer serviço que implemente o contrato OpenAI.

## 3. Model ID, aliases e allowlist

### 3.1 Formato canônico

```
provider/model
```

Exemplos:

```
ollama/llama3.1
ollama_cloud/qwen3-coder:480b
openai/gpt-4o-mini
anthropic/claude-sonnet-4-5
google/gemini-2.5-flash
openai_compatible/llama-3.3-70b-versatile
```

Regras:

- O prefixo é o **ID do provedor** (`ollama`, `openai`, etc.).
- O restante é o nome do modelo **como o provedor espera**.
- Nomes sem prefixo resolvem para `AI_DEFAULT_MODEL` ou para um alias.
- O nome resolvido aparece em `model` (nome no provedor) e `model_id`
  (canônico) nas respostas.
- Para Ollama, nome sem tag resolve para `:latest`.

### 3.2 Aliases

`AI_MODEL_ALIASES` é um JSON de nomes amigáveis para Model IDs:

```env
AI_MODEL_ALIASES={"fast":"ollama/llama3.1","smart":"anthropic/claude-sonnet-4-5","cheap":"openai/gpt-4o-mini"}
```

O cliente pode enviar `"model": "smart"`; a resposta traz
`model_id: "anthropic/claude-sonnet-4-5"`.

### 3.3 Allowlist

`AI_ALLOWED_MODELS` é uma lista separada por vírgulas com suporte a glob:

```env
AI_ALLOWED_MODELS=ollama/*,openai/gpt-4o-mini,anthropic/claude-*,google/gemini-2.5-*
```

- Vazio = todos os modelos dos provedores habilitados.
- O modelo padrão do servidor (`AI_DEFAULT_MODEL`) é **sempre** permitido.
- Modelo fora da allowlist retorna `422 model_not_allowed` **sem consumir cota**.

### 3.4 Fallback

`AI_FALLBACK_CHAIN` lista Model IDs alternativos, tentados **somente antes do
primeiro token**:

```env
AI_FALLBACK_CHAIN=openai/gpt-4o-mini,ollama/llama3.1
```

Regras:

- A cota é debitada **uma única vez**.
- Se o primeiro provedor falhar antes de produzir conteúdo, o próximo é tentado.
- Se a falha ocorrer depois do primeiro trecho, não há fallback: o cliente recebe
  o evento de erro.
- O fallback não é aplicado quando o erro é de validação, cota ou capacidade.

## 4. Configuração

### 4.1 Ollama local

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_API_KEY=
OLLAMA_MODEL=llama3.1
AI_DEFAULT_MODEL=ollama/llama3.1
```

- `OLLAMA_URL` deve começar com `http://` ou `https://`.
- `OLLAMA_API_KEY` é opcional e só é enviado se definido.
- `OLLAMA_MODEL` é mantido como alias legado de `AI_DEFAULT_MODEL=ollama/<modelo>`.

### 4.2 Ollama Cloud

```env
OLLAMA_CLOUD_URL=https://ollama.com
OLLAMA_CLOUD_API_KEY=...
OLLAMA_CLOUD_DEFAULT_MODEL=qwen3-coder:480b
```

O adapter é o mesmo do Ollama local, parametrizado por base URL e Bearer token.
Modelos disponíveis dependem da conta do Ollama Cloud.

### 4.3 OpenAI

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_DEFAULT_MODEL=gpt-4o-mini
```

- `OPENAI_BASE_URL` permite Azure OpenAI ou proxy compatível.
- `OPENAI_ORG_ID` é opcional, quando o provedor exigir.

### 4.4 Anthropic

```env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_VERSION=2023-06-01
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5
```

O header `anthropic-version` é obrigatório no protocolo e é enviado pela API.

### 4.5 Google Gemini

```env
GOOGLE_API_KEY=...
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com
GOOGLE_DEFAULT_MODEL=gemini-2.5-flash
```

- A API usa `x-goog-api-key` e os endpoints `generateContent`/`streamGenerateContent`.
- O mapeamento de `safetySettings` e `generationConfig` é documentado em
  [`compatibilidade-apis-externas.md`](compatibilidade-apis-externas.md).

### 4.6 OpenAI-compatible

```env
OPENAI_COMPAT_NAME=groq
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=gsk_...
OPENAI_COMPAT_DEFAULT_MODEL=llama-3.3-70b-versatile
```

Use para qualquer provedor que fale o contrato OpenAI. O nome lógico aparece no
catálogo e nos logs (`openai_compatible:groq`).

### 4.7 Habilitação de provedores

```env
AI_ENABLED_PROVIDERS=ollama,openai,anthropic
```

- Vazio = autodetectar: `ollama` sempre; os demais quando a credencial existir.
- Um provedor listado sem credencial/base URL **impede o boot** (`RF-GTW-03`).
- O `fake` só é habilitado explicitamente em testes.

### 4.8 Timeouts e limites

```env
AI_TIMEOUT_SECONDS=120
AI_STREAM_IDLE_TIMEOUT_SECONDS=60
AI_MAX_OUTPUT_TOKENS=1024
AI_MAX_OUTPUT_SECONDS=300
AI_MAX_RETRIES=1
MODELS_CACHE_SECONDS=10
```

- `AI_TIMEOUT_SECONDS` vale para chamadas não-streaming.
- `AI_STREAM_IDLE_TIMEOUT_SECONDS` aborta streams sem dados.
- `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS` são impostos pela API e
  resultam em `finish_reason=length|timeout`.
- `AI_MAX_RETRIES` limita as tentativas de fallback.

## 5. Capacidades

Cada adapter declara capacidades; o router valida antes de chamar o provedor.

| Capacidade | Descrição | Exemplo de uso |
|---|---|---|
| `chat` | Chat básico | qualquer requisição |
| `stream` | Streaming incremental | `stream=true` |
| `tools` | Ferramentas/function calling | Claude Code, agentes |
| `vision` | Imagens no contexto | análise de imagens |
| `json_mode` | Saída JSON estruturada | integrações |
| `context_window` | Janela máxima (quando conhecida) | validação de contexto |

Regras:

- `tools` depende do modelo; o adapter pode declarar `tools=true` e o catálogo
  marcar modelos específicos como incompatíveis.
- `json_mode` na Anthropic é emulado por instrução de sistema + validação.
- Incompatibilidade retorna `422 unsupported_capability` **sem consumir cota**.
- A capacidade efetiva é a interseção entre adapter, modelo e requisição.

## 6. Catálogo agregado

`GET /api/v1/models` retorna:

- `default_model`: Model ID padrão;
- `providers[]`: um item por provedor habilitado, com `available`, `default_model`
  e `models[]`;
- `models[]`: índice plano com `model_id`, `provider`, `name` e `selectable`.

Metadados por modelo: `size_bytes`, `modified_at`, `family`, `parameter_size`,
`quantization_level`, `capabilities`, `context_window`.

- Cache: `MODELS_CACHE_SECONDS` (padrão 10 s); `?refresh=true` ignora o cache.
- Consultar o catálogo **não** consome cota.
- Falha de um provedor: `available=false` e catálogo dos demais preservado.
- Falha total: `503 provider_unavailable`.

## 7. Saúde

- `GET /health/ready` consulta todos os provedores habilitados e o SQLite.
- `GET /api/v1/admin/ai/status` mostra `provider`, `default_model`, `available`
  e `checked_at` por provedor, sem chaves.
- O health de cada adapter usa uma chamada leve (ex.: `/api/tags`, `/models`).
- Timeout de health é curto (padrão 5 s) e não afeta o tráfego normal.

## 8. Normalização de erros

| Situação | Exceção interna | HTTP nativo | OpenAI | Anthropic | Ollama |
|---|---|---|---|---|---|
| Provedor fora do ar | `ProviderUnavailable` | 503 `provider_unavailable` | `api_error` | `api_error` | `{"error": "..."}` |
| Timeout | `ProviderTimeout` | 504 `ai_provider_timeout` | `api_error` | `api_error` | `{"error": "..."}` |
| Erro do provedor | `ProviderError` | 502 `ai_provider_error` | `api_error` | `api_error` | `{"error": "..."}` |
| Modelo inválido | `ModelNotFound` | 422 `model_not_found` | `invalid_request_error` | `invalid_request_error` | `{"error": "..."}` |
| Modelo fora da allowlist | `ModelNotAllowed` | 422 `model_not_allowed` | `invalid_request_error` | `invalid_request_error` | `{"error": "..."}` |
| Capacidade ausente | `UnsupportedCapability` | 422 `unsupported_capability` | `invalid_request_error` | `invalid_request_error` | `{"error": "..."}` |
| Cota/rate limit | `QuotaExceeded` | 429 com `code` | `rate_limit_error` | `rate_limit_error` | `{"error": "..."}` |

Regras:

- Nenhuma mensagem de erro inclui chave, header, URL interna ou corpo bruto do
  provedor.
- O `request_id` da NEXA é preservado nas respostas de compatibilidade quando o
  formato permitir (ex.: header `x-request-id`).
- Erros após o início do stream vão no evento da superfície.

## 9. Segurança

- Credenciais somente em variáveis de ambiente/cofre; nunca no SQLite, no
  repositório ou em respostas.
- Logs registram `provider`, `model`, `surface`, `latency` e `status`, mas nunca
  a chave nem o conteúdo.
- Comparação de API keys de compatibilidade com `hmac.compare_digest`.
- O adapter não mantém credenciais além do necessário para a chamada.
- O `request_id` é propagado ao provedor quando o protocolo suportar.

## 10. Como adicionar um provedor

1. Criar `app/ai/providers/<nome>.py` implementando `AIProvider`.
2. Declarar `Capabilities` e mapear `generate`, `stream`, `health`,
   `list_models` e `count_tokens`.
3. Registrar no `registry` com o ID do provedor.
4. Adicionar as variáveis de ambiente em `config.py` e `.env.example`.
5. Adicionar testes de contrato com `respx` e um golden de resposta.
6. Atualizar este documento e o SRS (§3.1.2).

Não é necessário alterar rotas, cotas ou schemas nativos.

## 11. Checklist operacional

- [ ] `AI_DEFAULT_MODEL` aponta para um Model ID válido e permitido.
- [ ] Todos os provedores habilitados têm credencial/base URL.
- [ ] `AI_ALLOWED_MODELS` cobre apenas o que deve ser exposto.
- [ ] `AI_FALLBACK_CHAIN` (se usado) aponta para provedores disponíveis.
- [ ] `MODELS_CACHE_SECONDS` é compatível com a frequência de mudança de modelos.
- [ ] O health check está verde em `/health/ready`.
- [ ] Nenhuma chave aparece em log (`grep` de auditoria).
