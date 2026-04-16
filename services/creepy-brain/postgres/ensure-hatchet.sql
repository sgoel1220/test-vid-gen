-- Idempotent Hatchet user and database creation.
-- Safe to run on every startup; skips objects that already exist.
SELECT 'CREATE USER hatchet WITH PASSWORD ''hatchet'''
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'hatchet')\gexec

SELECT 'CREATE DATABASE hatchet OWNER hatchet'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hatchet')\gexec

GRANT ALL PRIVILEGES ON DATABASE hatchet TO hatchet;
