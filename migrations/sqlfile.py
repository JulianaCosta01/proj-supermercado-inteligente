"""Executa um arquivo .sql inteiro pelo driver (sem interpolação de parâmetros)."""
import os
import pathlib
from alembic import op

SQL_DIR = pathlib.Path(os.environ.get("STP_SQL_DIR", pathlib.Path(__file__).parent / "sql"))


def executar(nome: str) -> None:
    sql = (SQL_DIR / nome).read_text(encoding="utf-8")
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(sql)
