# Matriz CA → teste — Nexa API v1.0.0

Rastreabilidade dos critérios de aceitação (SRS v1.1, §4) para os testes
automatizados. Execute a matriz completa com:

```bash
.venv/bin/pytest -q
```

| CA | Critério (resumo) | Testes | Status |
|---|---|---|---|
| CA-01 | Boot exige `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH` válidos | `tests/unit/test_config.py::test_admin_password_hash_is_required`, `test_jwt_secret_is_required`, `test_empty_admin_username_is_rejected`; `tests/integration/test_auth.py::test_login_success_returns_admin_token` | ✅ |
| CA-02 | Login correto devolve token; senha errada devolve `401` genérico | `tests/integration/test_auth.py::test_login_success_returns_admin_token`, `test_login_rejects_wrong_password`, `test_login_rejects_unknown_user_indistinguishably` | ✅ |
| CA-03 | Guest obtém token e usa o chat sem cadastro | `tests/integration/test_auth.py::test_guest_token_returns_gid_and_usage`; `tests/integration/test_chat.py::test_guest_json_chat_success` | ✅ |
| CA-04 | `stream=true` entrega SSE incremental e termina com `done` | `tests/integration/test_chat.py::test_guest_stream_success`, `test_stream_is_the_default_mode`; `tests/unit/test_chat_stream.py::test_stream_yields_data_chunks_then_done` | ✅ |
| CA-05 | `stream=false` devolve a resposta completa em JSON | `tests/integration/test_chat.py::test_guest_json_chat_success`, `test_admin_json_chat_has_no_daily_headers` | ✅ |
| CA-06 | Nada é persistido; conteúdo não aparece em log em nenhum nível | `tests/integration/test_chat.py::test_chat_does_not_log_message_content`, `test_chat_does_not_log_content_at_debug_level`, `test_stream_does_not_log_message_content`; `tests/integration/test_auth.py::test_authorization_token_is_never_logged`; `tests/unit/test_logging.py` | ✅ |
| CA-07 | Cota diária esgotada → `429 guest_daily_limit_reached` com `resets_at`, sem chamar o provedor | `tests/integration/test_chat.py::test_guest_daily_limit_returns_429_with_resets_at`, `test_ip_daily_limit_returns_specific_code`; `tests/unit/test_usage_store.py::test_gid_daily_limit_is_enforced_without_consuming`, `test_both_quotas_exhausted_prioritizes_gid_code` | ✅ |
| CA-08 | Limite por minuto → `429` com `Retry-After` | `tests/integration/test_chat.py::test_rate_limit_returns_retry_after_and_recovers`, `test_admin_rate_limit_has_retry_after_without_daily_headers`, `test_stream_keeps_rate_limit_headers_on_429` | ✅ |
| CA-09 | Novo token renova o `gid`, mas a cota por IP continua valendo (menor valor) | `tests/integration/test_auth.py::test_guest_usage_reflects_most_restrictive_quota`; `tests/unit/test_usage_store.py::test_ip_quota_is_shared_between_guests`, `test_snapshot_uses_lower_ip_quota` | ✅ |
| CA-10 | Emissão de token limitada por IP com `429 guest_token_limit_reached` + `Retry-After` | `tests/integration/test_auth.py::test_guest_token_issuance_is_limited_by_ip`, `test_guest_token_limit_uses_forwarded_ip_only_from_trusted_proxy`, `test_forwarded_ip_is_ignored_when_proxy_is_not_trusted` | ✅ |
| CA-11 | `/usage` coerente com os headers `X-RateLimit-*` | `tests/integration/test_usage_api.py` (8 testes); `tests/unit/test_usage.py::test_rate_limit_headers_from_usage` | ✅ |
| CA-12 | Admin sem cota diária; guest recebe `403` em `/admin/*` | `tests/integration/test_chat.py::test_admin_json_chat_has_no_daily_headers`; `tests/integration/test_admin.py::test_guest_is_forbidden_on_admin_reads`, `test_guest_is_forbidden_on_admin_reset`; `tests/integration/test_auth.py::test_guest_can_access_guest_route_but_not_admin`, `test_admin_can_access_admin_route` | ✅ |
| CA-13 | Falha antes do primeiro trecho devolve cota do `gid` **e** do IP | `tests/integration/test_chat.py::test_provider_failure_refunds_gid_and_ip`, `test_provider_failures_are_mapped_and_refund_quota`, `test_stream_error_before_first_chunk_refunds_quota`; `tests/unit/test_usage_store.py::test_refund_restores_gid_and_ip` | ✅ |
| CA-14 | Ollama parado → `502`/`504`/evento `error`; demais endpoints funcionam | `tests/integration/test_chat.py::test_provider_failures_are_mapped_and_refund_quota`, `test_other_endpoints_keep_working_when_the_provider_fails`, `test_stream_error_before_first_chunk_refunds_quota`; `tests/integration/test_system.py::test_readiness_with_unhealthy_provider`; `tests/unit/test_ollama.py` (mapeamento de erros) | ✅ |
| CA-15 | Troca de provedor por configuração, validada com fake na factory | `tests/unit/test_ai_factory.py::test_ca15_swap_provider_without_changing_routes`, `test_factory_builds_fake_by_name`, `test_create_app_uses_injected_provider`, `test_factory_rejects_cloud_with_clear_error` | ✅ |
| CA-16 | Login bloqueado após `LOGIN_MAX_ATTEMPTS` falhas | `tests/integration/test_auth.py::test_login_locks_after_max_attempts`, `test_successful_login_resets_failures`, `test_login_lock_uses_forwarded_ip_from_trusted_proxy` | ✅ |
| CA-17 | OpenAPI com todos os endpoints, exemplos e os dois tipos de resposta do chat | `tests/integration/test_openapi_contract.py` (snapshot + completude + exemplos + SSE/JSON + Bearer); `tests/integration/test_admin.py::test_admin_endpoints_are_documented_with_examples` | ✅ |
| CA-18 | Cobertura ≥ 80% e pipeline de lint/tipagem verdes | `.github/workflows/ci.yml` (Ruff, mypy, `pytest --cov-fail-under=80`, artefato `coverage.xml`); execução local: 99% em `app/` | ✅ |
| CA-19 | Frontend lê `X-RateLimit-*`, `Retry-After` e `X-Request-ID` via CORS | `tests/integration/test_system.py::test_cors_preflight`, `test_cors_exposes_headers`; `tests/integration/test_chat.py::test_cors_exposes_quota_headers_on_429` | ✅ |
| CA-20 | Admin lista guests, obtém `gid`, zera cota (`204`); alvo inexistente (`404`) | `tests/integration/test_admin.py::test_guest_listing_exposes_gid_and_usage`, `test_reset_gid_restores_the_guest_quota`, `test_reset_unknown_target_returns_not_found` | ✅ |
| CA-21 | Geração interrompida em `AI_MAX_OUTPUT_TOKENS`/`AI_MAX_OUTPUT_SECONDS` com `finish_reason` | `tests/integration/test_chat.py::test_finish_reason_is_returned`; `tests/unit/test_chat_stream.py::test_finish_reason_length_and_timeout_are_forwarded`; `tests/unit/test_ollama.py::test_generate_total_deadline_finishes_with_timeout_reason`, `test_stream_total_deadline_ends_with_timeout_reason` | ✅ |
| CA-22 | `401` com `WWW-Authenticate: Bearer`; `422` com `errors[]` | `tests/unit/test_errors.py::test_api_error_handler_adds_bearer_challenge`, `test_validation_error_handler_maps_fields`; `tests/integration/test_system.py::test_validation_error_format`; `tests/integration/test_chat.py::test_invalid_payload_returns_422_with_errors` | ✅ |
| CA-23 | Recusa iniciar com `UVICORN_WORKERS > 1` sem `REDIS_URL` | `tests/unit/test_config.py::test_multiple_workers_require_redis`, `test_multiple_workers_with_redis_are_allowed` | ✅ |

## Lacunas fechadas nesta fase

- **CA-17**: não havia teste de contrato do OpenAPI nem esquema de segurança
  declarado; adicionados `tests/integration/test_openapi_contract.py`,
  `tests/snapshots/openapi.json` e `HTTPBearer` na documentação.
- **CA-06**: o teste de log cobria o nível padrão; acrescentado teste em
  `LOG_LEVEL=DEBUG`.
- **CA-14**: acrescentado teste garantindo que `/health` e `/usage` continuam
  funcionando após falha do provedor.

## Evidências fora do pytest

- **CA-04/05/07/11/12/20/21/22** também verificados manualmente com Ollama real
  em 28/10/2026 — ver [`aceitacao-manual-2026-10-28.md`](aceitacao-manual-2026-10-28.md).
- **CA-18**: relatório de cobertura publicado como artefato no CI.
- **CA-23**: validado por teste unitário de `Settings` (o processo nem inicia).
