-- 001_users.sql — tabela de usuários, ADMIN protegido e triggers (F13-02/F13-04)
-- O SQLite guarda SOMENTE usuários e controle de migrações (RF-USR-01).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  checksum    TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id                  TEXT PRIMARY KEY,
  username            TEXT NOT NULL COLLATE NOCASE,
  username_display    TEXT NOT NULL,
  email               TEXT COLLATE NOCASE,
  password_hash       TEXT NOT NULL,
  role                TEXT NOT NULL CHECK (role IN ('admin', 'user', 'guest')),
  is_test             INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1)),
  is_protected        INTEGER NOT NULL DEFAULT 0 CHECK (is_protected IN (0, 1)),
  is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  daily_message_limit INTEGER CHECK (daily_message_limit IS NULL OR daily_message_limit >= 0),
  rate_per_min        INTEGER CHECK (rate_per_min IS NULL OR rate_per_min > 0),
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  last_login_at       TEXT,
  expires_at          TEXT,
  CHECK (role <> 'admin' OR (is_protected = 1 AND is_test = 0)),
  CHECK (is_protected = 0 OR role = 'admin')
);

-- O admin nunca pode ser excluído (invariante 4).
CREATE TRIGGER IF NOT EXISTS trg_users_admin_no_delete
BEFORE DELETE ON users
WHEN OLD.is_protected = 1
BEGIN
  SELECT RAISE(ABORT, 'admin_protected');
END;

-- O admin nunca pode ser rebaixado, desativado ou marcado como teste (invariante 5).
CREATE TRIGGER IF NOT EXISTS trg_users_admin_no_demote
BEFORE UPDATE OF role, is_protected, is_active, is_test ON users
WHEN OLD.is_protected = 1
BEGIN
  SELECT RAISE(ABORT, 'admin_protected');
END;

-- Existe no máximo um admin (invariante 1).
CREATE TRIGGER IF NOT EXISTS trg_users_single_admin
BEFORE INSERT ON users
WHEN NEW.role = 'admin'
 AND EXISTS (SELECT 1 FROM users WHERE role = 'admin')
BEGIN
  SELECT RAISE(ABORT, 'only_one_admin_allowed');
END;

-- Todo admin inserido nasce protegido e não é usuário de teste (invariante 3).
CREATE TRIGGER IF NOT EXISTS trg_users_admin_insert_protected
BEFORE INSERT ON users
WHEN NEW.role = 'admin'
 AND (NEW.is_protected <> 1 OR NEW.is_test <> 0)
BEGIN
  SELECT RAISE(ABORT, 'admin_must_be_protected');
END;
