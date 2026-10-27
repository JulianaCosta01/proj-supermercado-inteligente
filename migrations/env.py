"""Alembic: migrações em SQL puro, executadas sempre como o papel dono stp_migrator."""
import os
from alembic import context
from sqlalchemy import create_engine, text

url = os.environ["STP_MIGRATION_URL"]          # ex.: postgresql+psycopg://migrador:...@host/stp


def run():
    engine = create_engine(url)
    with engine.connect() as conn:
        # Objetos sempre pertencem ao papel NOLOGIN, nunca ao login que executa a migração.
        conn.execute(text("SET ROLE stp_migrator"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS meta"))
        conn.execute(text("REVOKE ALL ON SCHEMA meta FROM PUBLIC"))
        conn.commit()
        context.configure(connection=conn, version_table_schema="meta", transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
        conn.commit()


run()
