-- Token vault database (DP-D5, SG-ADP-02): token -> encrypted PII value, reachable only by vault_service.
-- The main database's roles have no CONNECT here.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner') THEN CREATE ROLE app_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vault_service') THEN CREATE ROLE vault_service NOLOGIN; END IF;
END $$;

CREATE FUNCTION current_tenant() RETURNS text LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.current_tenant_id', true), '') $$;

CREATE TABLE pii_tokens (
  tenant_id  text NOT NULL,
  token      text NOT NULL,  -- global deterministic token (HMAC of the normalized value)
  kind       text NOT NULL,
  value_enc  bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, token)
);

ALTER TABLE pii_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE pii_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pii_tokens
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE pii_tokens OWNER TO app_owner;

DO $$
BEGIN
  EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO vault_service', current_database());
END $$;

GRANT SELECT, INSERT, DELETE ON pii_tokens TO vault_service;
