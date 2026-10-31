-- Habilita NVIDIA NIM nas chaves BYOK sem alterar as credenciais existentes.
CREATE TABLE user_provider_keys_new (
  user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider          TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic', 'google', 'ollama_cloud', 'nvidia', 'openai_compatible')),
  api_key_encrypted TEXT NOT NULL,
  base_url          TEXT,
  default_model     TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  last_used_at      TEXT,
  PRIMARY KEY (user_id, provider)
);
INSERT INTO user_provider_keys_new
  (user_id, provider, api_key_encrypted, base_url, default_model, created_at, updated_at, last_used_at)
SELECT user_id, provider, api_key_encrypted, base_url, default_model, created_at, updated_at, last_used_at
FROM user_provider_keys;
DROP TABLE user_provider_keys;
ALTER TABLE user_provider_keys_new RENAME TO user_provider_keys;
