"""Schema base: cadastro, capturas, estoque, alertas e invariantes."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlfile import executar

revision = "0001"
down_revision = None


def upgrade():
    executar("0001_base.sql")


def downgrade():
    raise RuntimeError("downgrade não suportado: restaurar backup")
