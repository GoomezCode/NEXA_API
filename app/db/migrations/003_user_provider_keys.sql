-- Chaves BYOK cifradas por usuário; valores em claro nunca entram no SQLite.
CREATE TABLE IF NOT EXISTS user_provider_keys (
  user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider          TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic', 'google', 'ollama_cloud', 'openai_compatible')),
  api_key_encrypted TEXT NOT NULL,
  base_url          TEXT,
  default_model     TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  last_used_at      TEXT,
  PRIMARY KEY (user_id, provider)
);
