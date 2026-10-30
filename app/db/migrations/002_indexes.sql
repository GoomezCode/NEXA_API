-- 002_indexes.sql — índices para autenticação, listagem por cursor e limpeza (F13-02)
-- Toda consulta crítica tem índice; ver docs/database-sqlite.md §3.2/§5.

-- Lookup de autenticação O(log n) (RNF-DES-06).
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username
  ON users(username);

-- E-mail único apenas quando informado.
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email
  ON users(email)
  WHERE email IS NOT NULL;

-- Filtros administrativos por papel/ativo.
CREATE INDEX IF NOT EXISTS ix_users_role_active
  ON users(role, is_active);

-- Limpeza de usuários de teste expirados (somente is_test = 1).
CREATE INDEX IF NOT EXISTS ix_users_test_expires
  ON users(is_test, expires_at)
  WHERE is_test = 1;

-- Paginação por cursor (created_at, id).
CREATE INDEX IF NOT EXISTS ix_users_created_id
  ON users(created_at, id);
