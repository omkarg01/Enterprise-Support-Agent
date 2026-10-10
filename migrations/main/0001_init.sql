-- Main database: tenants pinned to a region, tenant settings, append-only audit and the legal transcript archive.
-- NFR-1 (DP-ADP-01), NFR-2 (DP-ADP-02), NFR-3 (DP-ADP-03), AC-11.
-- Every tenant table has forced row-level security keyed on app.current_tenant_id, set per transaction with
-- SET LOCAL. Tables are owned by app_owner (not a superuser), so FORCE applies to the owner too.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_owner') THEN CREATE ROLE app_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN CREATE ROLE app_runtime NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'legal_archive') THEN CREATE ROLE legal_archive NOLOGIN; END IF;
END $$;

CREATE FUNCTION current_tenant() RETURNS text LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.current_tenant_id', true), '') $$;

-- Tenants -------------------------------------------------------------------------------------------------

CREATE TABLE tenants (
  id         text PRIMARY KEY,
  name       text NOT NULL,
  region     text NOT NULL CHECK (region IN ('US', 'EU')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION keep_tenant_region() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.region <> OLD.region THEN
    RAISE EXCEPTION 'tenant % is pinned to region %', OLD.id, OLD.region;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tenants_region_pinned BEFORE UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION keep_tenant_region();

-- Tenant settings (platform definitions live in code; DP-D12) -------------------------------------------

CREATE TABLE tenant_settings (
  tenant_id              text PRIMARY KEY REFERENCES tenants (id),
  approval_threshold_usd numeric(12, 2) NOT NULL DEFAULT 1000,
  senior_amount_usd      numeric(12, 2) NOT NULL DEFAULT 5000,
  blackout_windows       jsonb NOT NULL DEFAULT '[]',
  monthly_budget_usd     numeric(12, 2),
  evaluation_opt_in      boolean NOT NULL DEFAULT false,
  training_opt_in        boolean NOT NULL DEFAULT false,
  version                integer NOT NULL DEFAULT 1,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CHECK (approval_threshold_usd >= 0 AND senior_amount_usd >= approval_threshold_usd)
);

-- Optimistic concurrency: writers send version = the version they read + 1 and filter on the version they read.
CREATE FUNCTION next_settings_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.version <> OLD.version + 1 THEN
    RAISE EXCEPTION 'stale tenant settings for %: expected version %', OLD.tenant_id, OLD.version + 1;
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER tenant_settings_versioned BEFORE UPDATE ON tenant_settings
  FOR EACH ROW EXECUTE FUNCTION next_settings_version();

-- Append-only audit (DP-D3) -----------------------------------------------------------------------------

CREATE TABLE tool_audit_records (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  conversation_id text NOT NULL,
  user_id         text,
  tool            text NOT NULL,
  action          text NOT NULL,
  outcome         text NOT NULL,
  payload_enc     bytea,  -- personal details, encrypted under the user's data key (crypto-shredding)
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tool_audit_records_tenant_created ON tool_audit_records (tenant_id, created_at);

CREATE TABLE decision_records (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  conversation_id text NOT NULL,
  turn_id         text,
  decision        text NOT NULL,
  reason          text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decision_records_tenant_created ON decision_records (tenant_id, created_at);

CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;
CREATE TRIGGER tool_audit_records_append_only BEFORE UPDATE OR DELETE ON tool_audit_records
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER tool_audit_records_no_truncate BEFORE TRUNCATE ON tool_audit_records
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER decision_records_append_only BEFORE UPDATE OR DELETE ON decision_records
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER decision_records_no_truncate BEFORE TRUNCATE ON decision_records
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Legal transcript archive (DP-D8): 2 years, legal_archive role only, tokenized transcript ------------------

CREATE TABLE transcript_legal_archive (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  conversation_id text NOT NULL,
  transcript      jsonb NOT NULL,
  archived_at     timestamptz NOT NULL DEFAULT now(),
  retain_until    timestamptz NOT NULL DEFAULT now() + interval '2 years'
);

CREATE FUNCTION forbid_early_archive_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.retain_until > now() THEN
    RAISE EXCEPTION 'transcript % is retained until %', OLD.id, OLD.retain_until;
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER transcript_legal_archive_no_update BEFORE UPDATE ON transcript_legal_archive
  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER transcript_legal_archive_no_truncate BEFORE TRUNCATE ON transcript_legal_archive
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER transcript_legal_archive_retained BEFORE DELETE ON transcript_legal_archive
  FOR EACH ROW EXECUTE FUNCTION forbid_early_archive_delete();

-- Forced row-level security on every tenant table -----------------------------------------------------------

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
  USING (id = current_tenant()) WITH CHECK (id = current_tenant());

ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_settings
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

ALTER TABLE tool_audit_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tool_audit_records
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

ALTER TABLE decision_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON decision_records
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

ALTER TABLE transcript_legal_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_legal_archive FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON transcript_legal_archive
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

-- Ownership and grants ----------------------------------------------------------------------------------------

ALTER TABLE tenants OWNER TO app_owner;
ALTER TABLE tenant_settings OWNER TO app_owner;
ALTER TABLE tool_audit_records OWNER TO app_owner;
ALTER TABLE decision_records OWNER TO app_owner;
ALTER TABLE transcript_legal_archive OWNER TO app_owner;

DO $$
BEGIN
  EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_runtime, legal_archive', current_database());
END $$;

GRANT SELECT ON tenants TO app_runtime;
GRANT SELECT, INSERT, UPDATE ON tenant_settings TO app_runtime;
GRANT SELECT, INSERT ON tool_audit_records, decision_records TO app_runtime;
-- The runtime has no grant on the archive; only the legal archive role reads and writes it.
GRANT SELECT, INSERT ON transcript_legal_archive TO legal_archive;
