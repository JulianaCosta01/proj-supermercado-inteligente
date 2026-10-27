-- Executado uma vez por um superusuário (ou pelo provedor do banco).
-- Papéis sem LOGIN; os usuários de login herdam de um deles.
CREATE ROLE stp_migrator NOLOGIN;   -- dono do schema, usado só por migrações
CREATE ROLE stp_app      NOLOGIN;   -- API web
CREATE ROLE stp_worker   NOLOGIN;   -- processo de visão computacional
CREATE ROLE stp_bi       NOLOGIN;   -- leitura analítica (exportação para o Power BI)
ALTER ROLE stp_bi SET default_transaction_read_only = on;
CREATE EXTENSION IF NOT EXISTS btree_gist;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- v0.5.1: sem objetos temporários para ninguém além do dono (evita sombreamento de nomes).
REVOKE TEMPORARY ON DATABASE {db} FROM PUBLIC;
GRANT CREATE ON DATABASE {db} TO stp_migrator;
