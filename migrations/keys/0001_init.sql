-- Key store database (DP-D4, CR-ADP-05): wrapped per-user data keys, reachable only by key_service.
-- Deleting a user's rows crypto-shreds everything encrypted under that key. Keys are never updated;
-- rotation inserts a new key_version. Backups of this database are kept for 1 day (CR-D13).

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner') THEN CREATE ROLE app_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'key_service') THEN CREATE ROLE key_service NOLOGIN; END IF;
END $$;

CREATE FUNCTION current_tenant() RETURNS text LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.current_tenant_id', true), '') $$;

CREATE TABLE user_keys (
  tenant_id   text NOT NULL,
  user_id     text NOT NULL,
  key_version integer NOT NULL DEFAULT 1,
  wrapped_dek bytea NOT NULL,  -- user data key wrapped under the tenant master key
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id, key_version)
);

ALTER TABLE user_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_keys
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE user_keys OWNER TO app_owner;

DO $$
BEGIN
  EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO key_service', current_database());
END $$;

GRANT SELECT, INSERT, DELETE ON user_keys TO key_service;
