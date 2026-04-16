-- Create Hatchet database user and database
-- This runs once when the postgres container is first initialized.
CREATE USER hatchet WITH PASSWORD 'hatchet';
CREATE DATABASE hatchet OWNER hatchet;
GRANT ALL PRIVILEGES ON DATABASE hatchet TO hatchet;
