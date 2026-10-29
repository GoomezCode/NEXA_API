# Banco de dados SQLite — usuários, ADMIN inviolável e performance

> Documento de apoio ao [SRS v2.0](srs-api-chat-ia-v1.md) e ao
> [plano de implementação v2.0](plano-implementacao-api-chat-ia-v1.md).
> **Estado:** implementado na fase F13; a API em produção permanece na v1.1.0.

## 1. Objetivo e escopo

O SQLite existe na v2.0 **exclusivamente para usuários**:

- o **ADMIN permanente**, espelhado da configuração;
- **usuários de teste** criados rapidamente pelo administrador;
- o controle de **migrações** do schema.

**Não há** tabelas de conversas, mensagens, respostas, feedback, auditoria
persistente ou billing. O chat continua efêmero (RN-05, RD-14).

Metas de performance:

| Operação | Meta |
|---|---|
| Leitura por `username` | p95 ≤ 2 ms · p99 ≤ 5 ms |
| Escrita (criar/atualizar) | p95 ≤ 10 ms |
| Listagem paginada de 10 mil usuários | p95 ≤ 20 ms |
| Cache de autenticação | p95 ≤ 0,5 ms |
| Migrações no boot | ≤ 200 ms |
| Boot completo do banco | ≤ 1 s |

## 2. Invariantes do ADMIN

O ADMIN deve sobreviver a **limpeza, migração, erro de aplicação e até à perda do
arquivo do banco**.

| # | Invariante | Como é garantido |
|---|---|---|
| 1 | Existe exatamente um admin | `CHECK`, trigger `single_admin` e seed idempotente |
| 2 | ID fixo | `00000000-0000-0000-0000-000000000001` |
| 3 | `role = admin`, `is_test = 0`, `is_protected = 1` | `CHECK` + trigger de inserção |
| 4 | Não pode ser excluído | trigger `BEFORE DELETE` |
| 5 | Não pode ser rebaixado/desativado/marcado como teste | trigger `BEFORE UPDATE OF role, is_protected, is_active, is_test` |
| 6 | Não pode ser alterado por API | guarda em `UserService`/`api/users.py` → `409 admin_protected` |
| 7 | Login funciona sem o banco | `AuthService` valida admin pela configuração (fallback) |
| 8 | Migração nunca o remove | verificação pós-migração + restauração de backup |
| 9 | Limpeza nunca o atinge | `WHERE is_test = 1 AND expires_at < now` |
| 10 | Configuração é a fonte de verdade | seed/upsert a cada boot; senha e username vêm do env |

### 2.1 Fluxo de seed no boot

```
1. ler ADMIN_USERNAME / ADMIN_PASSWORD_HASH
2. normalizar username (lowercase)
3. INSERT ... ON CONFLICT(id) DO UPDATE
   SET username, username_display, password_hash, updated_at
4. verificar que a linha existe com role=admin, is_protected=1, is_test=0
5. se falhar → restaurar backup e recusar o boot
```

O upsert **não** altera `role`, `is_protected`, `is_active` nem `is_test`; esses
campos são protegidos por trigger.

### 2.2 Fallback de autenticação do admin

```
POST /auth/login
  ├── username == ADMIN_USERNAME?
  │     ├── sim → verifica contra ADMIN_PASSWORD_HASH (config)
  │     │         ├── ok → emite JWT admin (não depende do SQLite)
  │     │         └── falha → 401 genérico
  │     └── não → consulta SQLite (usuário de teste)
  └── SQLite indisponível?
        ├── admin → ainda autentica pela config
        └── usuário de teste → 503 database_unavailable
```

## 3. Schema

### 3.1 Migração `001_users.sql`

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  checksum    TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id                 TEXT PRIMARY KEY,
  username           TEXT NOT NULL COLLATE NOCASE,
  username_display   TEXT NOT NULL,
  email              TEXT COLLATE NOCASE,
  password_hash      TEXT NOT NULL,
  role               TEXT NOT NULL CHECK (role IN ('admin', 'user', 'guest')),
  is_test            INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1)),
  is_protected       INTEGER NOT NULL DEFAULT 0 CHECK (is_protected IN (0, 1)),
  is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  daily_message_limit INTEGER CHECK (daily_message_limit IS NULL OR daily_message_limit >= 0),
  rate_per_min       INTEGER CHECK (rate_per_min IS NULL OR rate_per_min > 0),
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  last_login_at      TEXT,
  expires_at         TEXT,
  CHECK (role <> 'admin' OR (is_protected = 1 AND is_test = 0)),
  CHECK (is_protected = 0 OR role = 'admin')
);
```

### 3.2 Migração `002_indexes.sql`

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username
  ON users(username);

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email
  ON users(email)
  WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_users_role_active
  ON users(role, is_active);

CREATE INDEX IF NOT EXISTS ix_users_test_expires
  ON users(is_test, expires_at)
  WHERE is_test = 1;

CREATE INDEX IF NOT EXISTS ix_users_created_id
  ON users(created_at, id);
```

### 3.3 Triggers de proteção

```sql
CREATE TRIGGER IF NOT EXISTS trg_users_admin_no_delete
BEFORE DELETE ON users
WHEN OLD.is_protected = 1
BEGIN
  SELECT RAISE(ABORT, 'admin_protected');
END;

CREATE TRIGGER IF NOT EXISTS trg_users_admin_no_demote
BEFORE UPDATE OF role, is_protected, is_active, is_test ON users
WHEN OLD.is_protected = 1
BEGIN
  SELECT RAISE(ABORT, 'admin_protected');
END;

CREATE TRIGGER IF NOT EXISTS trg_users_single_admin
BEFORE INSERT ON users
WHEN NEW.role = 'admin'
 AND EXISTS (SELECT 1 FROM users WHERE role = 'admin')
BEGIN
  SELECT RAISE(ABORT, 'only_one_admin_allowed');
END;

CREATE TRIGGER IF NOT EXISTS trg_users_admin_insert_protected
BEFORE INSERT ON users
WHEN NEW.role = 'admin'
 AND (NEW.is_protected <> 1 OR NEW.is_test <> 0)
BEGIN
  SELECT RAISE(ABORT, 'admin_must_be_protected');
END;
```

> **Observação:** o trigger de update não inclui `username` nem `password_hash`,
> pois o boot precisa sincronizá-los com a configuração. A guarda da aplicação
> impede que a API altere esses campos do admin.

### 3.4 Seed do admin

```sql
INSERT INTO users (
  id, username, username_display, email, password_hash,
  role, is_test, is_protected, is_active, created_at, updated_at
) VALUES (?, ?, ?, NULL, ?, 'admin', 0, 1, 1, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  username = excluded.username,
  username_display = excluded.username_display,
  password_hash = excluded.password_hash,
  updated_at = excluded.updated_at;
```

Se o `username` do env colidir com outro usuário, o índice único falha e o boot
é recusado com mensagem clara (`admin_username_conflict`).

## 4. PRAGMAs e conexão

### 4.1 PRAGMAs de inicialização

| PRAGMA | Valor | Motivo |
|---|---|---|
| `journal_mode` | `WAL` | Leituras concorrentes com escrita |
| `synchronous` | `NORMAL` | Equilíbrio durabilidade/performance; `FULL` disponível |
| `foreign_keys` | `ON` | Integridade referencial |
| `busy_timeout` | `5000` | Evita `SQLITE_BUSY` imediato |
| `cache_size` | `-65536` (64 MB) | Cache de páginas |
| `mmap_size` | `268435456` (256 MB) | Leitura via mmap |
| `temp_store` | `MEMORY` | Temporários em memória |
| `wal_autocheckpoint` | `1000` | Controla crescimento do WAL |
| `query_only` | `ON` (somente leitura) | Pool de leitura não escreve |

### 4.2 Estratégia de conexões

```
┌─────────────────────────────┐
│ escrita (1 conexão)         │  BEGIN IMMEDIATE, asyncio.Lock
│  ├─ create/update/delete    │  transações curtas
│  └─ seed/migração           │
└─────────────────────────────┘
┌─────────────────────────────┐
│ leitura (pool N=4)          │  PRAGMA query_only=ON
│  ├─ get_by_username         │  WAL permite leitura concorrente
│  ├─ get_by_id               │
│  └─ list/stats              │
└─────────────────────────────┘
```

- `aiosqlite` para não bloquear o event loop.
- Escritas serializadas por `asyncio.Lock` e `BEGIN IMMEDIATE`.
- Leituras usam conexões do pool, sem lock global.
- Transações de escrita devem ser curtas (sem chamadas de rede/hash dentro).
- O hash da senha é calculado **antes** de abrir a transação.

### 4.3 Inicialização

```python
# app/db/connection.py (referência)
async def open_database(settings: Settings) -> Database:
    path = sqlite_path(settings.database_url)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    writer = await aiosqlite.connect(path, isolation_level=None)
    await writer.execute("PRAGMA journal_mode=WAL")
    await writer.execute("PRAGMA synchronous=NORMAL")
    await writer.execute("PRAGMA foreign_keys=ON")
    await writer.execute("PRAGMA busy_timeout=5000")
    await writer.execute("PRAGMA cache_size=-65536")
    await writer.execute("PRAGMA mmap_size=268435456")
    await writer.execute("PRAGMA temp_store=MEMORY")
    readers = [await open_reader(path) for _ in range(settings.sqlite_read_pool_size)]
    return Database(writer=writer, readers=readers)
```

## 5. Repositório e consultas

### 5.1 Regras

- SQL **sempre parametrizado**; proibida concatenação de entrada.
- Colunas explícitas; nunca `SELECT *`.
- Paginação por cursor (`created_at`, `id`), não `OFFSET` em tabelas grandes.
- `LIMIT` máximo de 100.
- Sem N+1: listagens retornam apenas as colunas necessárias.
- Toda consulta crítica tem `EXPLAIN QUERY PLAN` coberto por teste.
- `last_login_at` é atualizado em escrita separada e curta (não bloqueia o login).

### 5.2 Consultas principais

```sql
-- autenticação
SELECT id, username, username_display, password_hash, role,
       is_test, is_protected, is_active, daily_message_limit, rate_per_min,
       expires_at
FROM users
WHERE username = ? AND is_active = 1;

-- listagem por cursor
SELECT id, username, username_display, email, role, is_test, is_active,
       daily_message_limit, rate_per_min, created_at, last_login_at, expires_at
FROM users
WHERE (? IS NULL OR (created_at, id) > (?, ?))
ORDER BY created_at, id
LIMIT ?;

-- limpeza
DELETE FROM users
WHERE is_test = 1
  AND is_protected = 0
  AND expires_at IS NOT NULL
  AND expires_at < ?;
```

### 5.3 Cache de autenticação

- Chave: `username` normalizado.
- Valor: `UserRow` + `expires_at` do cache.
- TTL: `USERS_CACHE_TTL_SECONDS` (padrão 30 s).
- Teto: `USERS_CACHE_MAX_ENTRIES` (LRU).
- Invalidação explícita em: criação, atualização, redefinição de senha,
  desativação e exclusão.
- **Sem cache negativo** (evita bloquear usuário recém-criado).
- O admin **não** usa o cache para autenticar (fallback direto da config).

## 6. Migrações

### 6.1 Regras

- Arquivos SQL numerados (`001_users.sql`, `002_indexes.sql`, ...).
- Cada arquivo tem checksum registrado em `schema_migrations`.
- Execução em transação; falha → rollback e boot recusado.
- Antes de migrar: backup (`DB_BACKUP_BEFORE_MIGRATION=true`).
- Migrações **nunca** removem usuários nem o admin.
- Após migrar: verificar admin; se ausente/inválido, restaurar backup e recusar.
- Teste de upgrade a partir de banco vazio e de banco v1.1.0.

### 6.2 Runner

```
1. abrir conexão de escrita
2. criar schema_migrations (se não existir)
3. listar versões aplicadas
4. para cada arquivo pendente:
   ├── validar checksum dos já aplicados
   ├── BEGIN IMMEDIATE
   ├── executar SQL
   ├── INSERT INTO schema_migrations
   └── COMMIT
5. ensure_admin()
6. verificar admin
```

### 6.3 Backup

```bash
# consistente, com WAL
sqlite3 ./data/nexa.db ".backup './data/backups/nexa-20261029T143000Z.db'"

# alternativa: cópia compacta
sqlite3 ./data/nexa.db "VACUUM INTO './data/backups/nexa-20261029T143000Z.db'"
```

- Diretório de backup com permissão `0700`; arquivos `0600`.
- Retenção sugerida: 7 diários + 4 semanais.
- Restauração: parar a API, substituir o arquivo, manter o WAL/SHM coerentes
  (remover `-wal`/`-shm` antigos), iniciar e validar o admin.

## 7. Usuários de teste

### 7.1 Criação

```json
POST /api/v1/admin/users
{
  "username": "teste01",
  "password": "opcional-ou-gerada",
  "email": "teste01@example.com",
  "daily_message_limit": 100,
  "rate_per_min": 10,
  "expires_at": "2026-11-05T14:30:00Z"
}
```

- `role` é sempre `user`; `is_test` é sempre `1`.
- Senha gerada com pelo menos 16 caracteres aleatórios quando omitida.
- A senha é retornada **uma única vez** (`Cache-Control: no-store`) e nunca logada.
- `username` é normalizado para lowercase; `username_display` preserva o original.
- `expires_at` padrão: `now + TEST_USER_TTL_HOURS` (0 = sem expiração).

### 7.2 Criação em lote

```json
POST /api/v1/admin/users/bulk
{
  "prefix": "teste",
  "count": 20,
  "daily_message_limit": 50,
  "rate_per_min": 5,
  "expires_at": "2026-11-05T14:30:00Z"
}
```

- `count` ≤ `TEST_USER_MAX_BULK` (padrão 100).
- Usernames sequenciais (`teste01`...`teste20`).
- Resposta com a lista de credenciais geradas, uma única vez.
- Inserção em uma transação (`executemany`).

### 7.3 Ciclo de vida

| Ação | Endpoint | Regra |
|---|---|---|
| Criar | `POST /admin/users` | `role=user`, `is_test=1` |
| Criar em lote | `POST /admin/users/bulk` | limite de lote |
| Listar | `GET /admin/users` | cursor + filtros |
| Consultar | `GET /admin/users/{id}` | sem hash |
| Atualizar | `PATCH /admin/users/{id}` | não altera `role`/`is_test`/admin |
| Resetar senha | `POST /admin/users/{id}/reset-password` | invalida cache |
| Excluir | `DELETE /admin/users/{id}` | somente `is_test=1` |
| Limpar expirados | `POST /admin/users/cleanup` | `is_test=1 AND expires_at < now` |

### 7.4 Limpeza automática

- Task de background no `lifespan`, a cada 15 minutos.
- Remove apenas `is_test = 1 AND is_protected = 0 AND expires_at < now`.
- Loga a quantidade removida (sem dados pessoais).
- O admin nunca é candidato, mesmo que `expires_at` seja nulo ou inválido.

## 8. Performance

### 8.1 O que garante velocidade

1. **WAL**: leituras não bloqueiam a escrita.
2. **Índice único em `username`**: lookup O(log n).
3. **Cache de autenticação**: evita ida ao banco em requisições repetidas.
4. **Pool de leitura**: concorrência sem lock global.
5. **Escrita serializada**: evita `SQLITE_BUSY` e contenção.
6. **Colunas explícitas + cursor**: menos I/O e memória.
7. **`mmap_size` + `cache_size`**: páginas quentes em memória.
8. **`PRAGMA optimize`**: estatísticas atualizadas no shutdown/manutenção.

### 8.2 Benchmark

`scripts/db_benchmark.py`:

```
1. cria banco temporário com schema v2
2. insere 10.000 usuários de teste
3. mede 10.000 leituras por username (p50/p95/p99)
4. mede 1.000 escritas (p50/p95/p99)
5. mede 100 listagens paginadas
6. mede `EXPLAIN QUERY PLAN` das consultas críticas
7. falha se p95 exceder as metas
```

Metas:

| Operação | p95 | p99 |
|---|---|---|
| Leitura por username | ≤ 2 ms | ≤ 5 ms |
| Escrita | ≤ 10 ms | ≤ 25 ms |
| Listagem (10k) | ≤ 20 ms | ≤ 50 ms |

> Benchmarks rodam em job separado no CI e em ambiente controlado; variações de
> hardware são registradas no relatório.

### 8.3 Manutenção

- `PRAGMA optimize` no shutdown (`DB_OPTIMIZE_ON_SHUTDOWN=true`).
- `PRAGMA wal_checkpoint(TRUNCATE)` em janela de manutenção, não no boot.
- `VACUUM` apenas em manutenção programada (não no boot).
- Monitorar tamanho do WAL; checkpoint se crescer além de ~100 MB.
- `quick_check` no boot (`DB_STARTUP_CHECK=quick`); `full` em manutenção.

## 9. Segurança

| Item | Regra |
|---|---|
| Arquivo do banco | permissão `0600`; diretório `0700` |
| Backups | mesma permissão; criptografia opcional no storage |
| Senhas | argon2id/bcrypt; nunca texto puro |
| Hash | nunca retornado por API ou log |
| SQL | sempre parametrizado |
| API keys | não ficam no banco (env) |
| Admin | triggers + guarda de aplicação |
| Logs | sem username? (permitido), sem hash, sem senha, sem conteúdo |
| Acesso | somente o processo da API; sem exposição de porta |

## 10. Monitoramento e operação

`GET /api/v1/admin/db/status`:

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

Alertas sugeridos:

- `read_latency_ms_p95 > 5` por 5 min.
- `write_latency_ms_p95 > 25` por 5 min.
- WAL > 100 MB.
- `quick_check` diferente de `ok`.
- Falha de migração ou admin ausente.

## 11. Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| `SQLITE_BUSY` | Escrita concorrente/transação longa | Verificar lock, encurtar transação, aumentar `busy_timeout` |
| Leituras lentas | Índice ausente/consulta sem filtro | `EXPLAIN QUERY PLAN`, criar índice |
| WAL crescendo | Checkpoint não acontecendo | `PRAGMA wal_checkpoint(TRUNCATE)` |
| Admin ausente | Migração/seed falhou | Restaurar backup, corrigir seed, reiniciar |
| `database is locked` no boot | Outro processo usando o arquivo | Garantir um único processo/instância |
| Arquivo corrompido | Disco/queda | Restaurar backup; `quick_check`/`full` |
| Login de teste 503 | Banco indisponível | Verificar arquivo/permissões; admin continua via config |
| Cache obsoleto | Invalidação não disparada | Verificar `UserService` e TTL |

## 12. Capacidade

| Usuários | Tamanho aproximado | Observação |
|---|---|---|
| 1.000 | ~1 MB | trivial |
| 10.000 | ~5–10 MB | meta de benchmark |
| 100.000 | ~50–100 MB | ainda adequado; monitorar listagens |
| 1.000.000 | ~0,5–1 GB | fora do escopo; considerar outro banco |

O SQLite é adequado para o escopo de **usuários** desta versão. Se o produto
passar a persistir histórico de conversas em larga escala, a arquitetura deve
migrar para um banco cliente-servidor (fora do escopo da v2.0).

## 13. Checklist de implementação

- [ ] `DATABASE_URL` validado como `sqlite:///`.
- [ ] Diretório/arquivo com permissões corretas.
- [ ] PRAGMAs aplicados em todas as conexões.
- [ ] WAL ativo e verificado.
- [ ] Migrações `001`/`002` idempotentes e com checksum.
- [ ] Triggers de proteção do admin criados.
- [ ] Seed do admin idempotente e verificado.
- [ ] Fallback de login do admin sem banco.
- [ ] Repositório com SQL parametrizado e colunas explícitas.
- [ ] Paginação por cursor.
- [ ] Cache com TTL, teto e invalidação.
- [ ] Criação/lote/reset/exclusão/limpeza de usuários de teste.
- [ ] Backup antes de migração e restauração documentada.
- [ ] `/admin/db/status` implementado.
- [ ] Benchmarks dentro das metas.
- [ ] Testes de admin inviolável (API + SQL direto).
- [ ] Nenhum conteúdo de chat no banco.
