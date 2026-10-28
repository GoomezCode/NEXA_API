# Aceitação manual com Ollama real — 28/10/2026

Rodada de aceitação da API v1.0.0 contra um Ollama local, sem provedor *fake*.
Complementa os testes automatizados da [`matriz CA → teste`](matriz-ca-testes.md).

## Ambiente

| Item | Valor |
|---|---|
| Data | 28/10/2026 |
| SO | Linux (Omarchy) |
| Python | 3.14.8 |
| uvicorn | 0.54.0 |
| Ollama | 0.40.7 em `http://127.0.0.1:11434` |
| Modelo | `qwen3-coder:latest` |
| Configuração extra | `GUEST_DAILY_MESSAGES=2`, `GUEST_RATE_PER_MIN=10`, `AI_MAX_OUTPUT_TOKENS=64`, `AI_MAX_OUTPUT_SECONDS=120` |

Servidor:

```bash
ADMIN_USERNAME=admin \
ADMIN_PASSWORD_HASH='<hash argon2id de "admin">' \
JWT_SECRET='<segredo de 32+ bytes>' \
OLLAMA_URL=http://127.0.0.1:11434 \
OLLAMA_MODEL=qwen3-coder:latest \
GUEST_DAILY_MESSAGES=2 GUEST_RATE_PER_MIN=10 AI_MAX_OUTPUT_TOKENS=64 \
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8093
```

## Resultados

| Passo | Comando (resumo) | Resultado |
|---|---|---|
| CA-01 | boot com hash válido | servidor subiu; sem credenciais o boot falha (teste unitário) |
| `/health` | `GET /health` | `200` `{"status":"ok","version":"1.1.0",...}` |
| `/health/ready` | `GET /health/ready` | `200` `{"status":"ok","provider":"ollama"}` |
| CA-02 | `POST /auth/login` | `200` com JWT de admin |
| CA-03 | `POST /auth/guest` | `200` com `gid`, `usage` (`daily_limit=2`) |
| CA-05 | chat `stream=false` pedindo “Diga apenas: ok” | `200` em ~20,4 s, `{"content":"ok"}`, `finish_reason="stop"`, tokens de uso coerentes |
| CA-04 | chat `stream=true` (`curl -N`) | chunks `data:{"content":...}` incrementais e `event: done` no fim |
| CA-21 | mesma resposta SSE com `AI_MAX_OUTPUT_TOKENS=64` | `finish_reason="length"`, sem erro |
| CA-11 | `GET /usage` após duas mensagens | `daily_remaining=0`, `per_minute_remaining=8` |
| CA-07 | terceira mensagem | `429` `guest_daily_limit_reached` com `resets_at` |
| CA-12/20 | `GET /admin/stats`, `/admin/ai/status`, `/admin/guests` | `200`; `chat_requests_total=3`, `rate_limited_total=1`, modelo e `gid` listados |
| CA-20 | `POST /admin/usage/reset` (`gid` da listagem) | `204`; alvo inexistente `404` |
| CA-12 | guest em `/admin/stats` | `403 forbidden` |
| CA-22 | `GET /usage` sem token | `401` com `WWW-Authenticate: Bearer` |
| CA-22 | chat com `messages: []` | `422` com `errors[]` |
| CA-06 | logs do servidor durante a rodada | nenhum token, senha ou conteúdo de mensagem registrado |
| — | `SIGTERM` no uvicorn | encerramento gracioso dentro do prazo |

Trechos representativos (tokens omitidos):

```json
{"message":{"role":"assistant","content":"ok"},"provider":"ollama",
 "model":"qwen3-coder:latest","finish_reason":"stop",
 "usage":{"daily_limit":2,"daily_remaining":1,"per_minute_limit":10,
          "per_minute_remaining":9,"resets_at":"2026-10-29T00:00:00Z"}}
```

```text
data: {"content":"Cl"}
data: {"content":"aro"}
...
event: done
data: {"status":"complete","finish_reason":"length","usage":{...}}
```

```json
{"type":"about:blank","title":"Limite diário excedido","status":429,
 "code":"guest_daily_limit_reached","detail":"A cota diária de mensagens foi atingida.",
 "request_id":"...","resets_at":"2026-10-29T00:00:00Z"}
```

## Observações

- `finish_reason="length"` na segunda resposta é o comportamento esperado do
  limite configurado para a rodada (CA-21), não uma falha.
- CA-14 (Ollama parado) não foi executado nesta rodada porque o serviço é
  compartilhado na máquina; está coberto pelo teste
  `test_other_endpoints_keep_working_when_the_provider_fails` com provedor fake.
- CA-23 (workers × Redis) é validação de boot, coberta por teste unitário.
