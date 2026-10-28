# Nexa API

Backend do chat de IA via web, em **Python/FastAPI**, sem banco de dados.

- **SRS:** [`docs/srs-api-chat-ia-v1.md`](docs/srs-api-chat-ia-v1.md) (v1.1)
- **Plano de implementação:** [`docs/plano-implementacao-api-chat-ia-v1.md`](docs/plano-implementacao-api-chat-ia-v1.md) (v1.1)

## Requisitos

- Python 3.11+ (o CI usa 3.12)
- `make` (opcional)

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

## Execução

```bash
make run
# equivalente a:
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8000
```

Endpoints disponíveis na fase atual:

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/v1/auth/login` | Login do administrador (JWT) |
| POST | `/api/v1/auth/guest` | Emite token de convidado (`gid` + `usage` inicial) |
| GET | `/api/v1/usage` | Limites e uso restante do chamador |
| POST | `/api/v1/chat` | Envia o contexto e recebe a resposta do modelo (JSON ou SSE) |
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

## Qualidade

```bash
make lint   # ruff check + ruff format --check
make type   # mypy strict
make test   # pytest
make cov    # pytest + cobertura (mínimo 80%)
make check  # tudo acima
```

## Estrutura

```text
app/
├── api/          # rotas HTTP
├── core/         # config auxiliar, erros, logging, middlewares, IP, clock
├── schemas/      # modelos Pydantic de entrada/saída
├── config.py     # Settings (12-factor, fail-fast)
└── main.py       # create_app() — sem instância global
scripts/          # utilitários (hash de senha)
tests/
├── unit/         # configuração, IP, erros, logging, middlewares, clock
└── integration/  # rotas ASGI com TestClient
```

## Fases

A implementação segue o plano: **F0 Fundação**, **F1 Autenticação**,
**F2 Provedores de IA**, **F3 Cotas e limites**, **F4 Chat JSON**,
**F5 Streaming SSE**, **F6 Administração**, **F7 Hardening** e
**F8 Observabilidade** (concluídas); F9 Empacotamento, F10 Conformidade.
