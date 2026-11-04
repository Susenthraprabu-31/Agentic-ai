-- Title Search Platform — PostgreSQL initialization
-- This runs once when the container is first created.

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create the application database (if not already created by POSTGRES_DB env var)
-- The main database is created by Docker via POSTGRES_DB env var.
-- This script adds extensions and any initial setup.

\echo 'PostgreSQL initialized for Title Search Platform'

