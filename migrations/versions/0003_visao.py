"""Visão: faces de gôndola, marcadores, geometria das posições e alinhamento."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlfile import executar

revision = "0003"
down_revision = "0002"


def upgrade():
    executar("0003_visao.sql")


def downgrade():
    raise RuntimeError("downgrade não suportado: restaurar backup")
