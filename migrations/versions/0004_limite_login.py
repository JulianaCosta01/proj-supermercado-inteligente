"""Limite de tentativas de login por conta, por IP e por conta+IP."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlfile import executar

revision = "0004"
down_revision = "0003"


def upgrade():
    executar("0004_limite_login.sql")


def downgrade():
    raise RuntimeError("downgrade não suportado: restaurar backup")
