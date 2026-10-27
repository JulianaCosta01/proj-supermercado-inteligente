"""Gate D: autenticação, códigos externos, imagem ausente, conclusão idempotente."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlfile import executar

revision = "0002"
down_revision = "0001"


def upgrade():
    executar("0002_gate_d.sql")


def downgrade():
    raise RuntimeError("downgrade não suportado: restaurar backup")
