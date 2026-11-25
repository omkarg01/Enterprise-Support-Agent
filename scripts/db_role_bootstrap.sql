GRANT USAGE, CREATE ON SCHEMA public TO CURRENT_USER;
DO 
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = app_owner) THEN CREATE ROLE app_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = app_runtime) THEN CREATE ROLE app_runtime NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = legal_archive) THEN CREATE ROLE legal_archive NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = vault_service) THEN CREATE ROLE vault_service NOLOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = key_service) THEN CREATE ROLE key_service NOLOGIN; END IF;
END ;
GRANT app_owner, app_runtime, legal_archive, vault_service, key_service TO CURRENT_USER;
