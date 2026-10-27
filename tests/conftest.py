"""Infra de testes: banco descartável criado pelas migrações Alembic reais.

Variáveis: STP_PG_HOST, STP_PG_PORT, STP_PG_USER (superusuário), STP_PG_PASSWORD, STP_TEST_DB.
"""
import os
import pathlib
import subprocess
import sys
import psycopg
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
H = os.environ.get("STP_PG_HOST", "/tmp/pg")
P = os.environ.get("STP_PG_PORT", "5433")
U = os.environ.get("STP_PG_USER", "postgres")
PW = os.environ.get("STP_PG_PASSWORD", "")
DB = os.environ.get("STP_TEST_DB", "stp")
SENHA_LOGINS = "teste-somente-local"   # logins descartáveis criados só no banco de teste


def dsn(db, user=U, pw=PW):
    s = f"host={H} port={P} user={user} dbname={db}"
    return s + (f" password={pw}" if pw else "")


DSN_ADMIN = dsn("postgres")
DSN = dsn(DB)
DSN_APP = dsn(DB, "stp_app_u", SENHA_LOGINS)
DSN_WORKER = dsn(DB, "stp_worker_u", SENHA_LOGINS)
PAPEIS = ("stp_migrator", "stp_app", "stp_worker", "stp_bi")
LOGINS = {"stp_app_u": "stp_app", "stp_worker_u": "stp_worker"}


def criar_banco(db=DB, sql_dir=None):
    """Recria o banco e os papéis e aplica as migrações (alembic upgrade head)."""
    with psycopg.connect(DSN_ADMIN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        for login in LOGINS:
            c.execute(f"DROP ROLE IF EXISTS {login}")
        for r in PAPEIS:
            existe = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone()
            if existe:
                c.execute(f"DROP ROLE {r}")
        c.execute(f"CREATE DATABASE {db}")
    with psycopg.connect(dsn(db), autocommit=True) as c:
        roles = pathlib.Path(os.environ.get("STP_ROLES_FILE", ROOT / "db" / "roles.sql"))
        c.execute(roles.read_text().replace("{db}", db))
        for login, papel in LOGINS.items():
            c.execute(f"CREATE ROLE {login} LOGIN PASSWORD '{SENHA_LOGINS}' IN ROLE {papel}")
    env = dict(os.environ, STP_MIGRATION_URL=f"postgresql+psycopg://{U}:{PW}@/{db}?host={H}&port={P}")
    if sql_dir:
        env["STP_SQL_DIR"] = str(sql_dir)
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.fixture(scope="session", autouse=True)
def banco():
    criar_banco(sql_dir=os.environ.get("STP_SQL_DIR"))
    yield


@pytest.fixture
def conn():
    """Conexão de superusuário; cada teste roda numa transação desfeita no fim."""
    with psycopg.connect(DSN) as c:
        yield c
        c.rollback()


def como(conn, papel):
    conn.execute(f"SET LOCAL ROLE {papel}")


def um(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


@pytest.fixture
def cenario(conn):
    """Loja real com uma posição, um produto, planograma, captura, observação vazia,
    snapshot com estoque > 0, um alerta D1 e usuários de cada papel."""
    como(conn, "stp_migrator")
    d = {}
    d["loja"] = um(conn, "INSERT INTO op.loja (nome, origem_dado) VALUES ('Loja A', 'real') RETURNING id")
    for papel in ("repositor", "gerente", "analista", "admin"):
        d[papel] = um(conn, "INSERT INTO op.usuario (loja_id, nome, papel) VALUES (%s, %s, %s) RETURNING id",
                      d["loja"], papel, papel)
    d["produto"] = um(conn, "INSERT INTO op.produto (loja_id, ean, descricao) VALUES (%s, '7891000100103', 'Café 500 g') RETURNING id", d["loja"])
    d["produto2"] = um(conn, "INSERT INTO op.produto (loja_id, ean, descricao) VALUES (%s, '7891000200207', 'Açúcar 1 kg') RETURNING id", d["loja"])
    d["posicao"] = um(conn, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'G01-N2-P03') RETURNING id", d["loja"])
    d["posicao2"] = um(conn, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'G01-N2-P04') RETURNING id", d["loja"])
    d["plan"] = um(conn, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL)) RETURNING id", d["posicao"], d["produto"])
    d["plan2"] = um(conn, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL)) RETURNING id", d["posicao2"], d["produto2"])
    d["metodo"] = um(conn, "INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES ('B1-diferenca', '0.1.0', 0.8) RETURNING id")
    d["captura"] = um(conn, "INSERT INTO op.captura (loja_id, sha256_original, quarentena_chave, capturado_em) VALUES (%s, repeat('a', 64), gen_random_uuid(), '2026-10-20 10:00-03') RETURNING id", d["loja"])
    d["obs"] = um(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.93) RETURNING id", d["captura"], d["posicao"], d["plan"], d["metodo"])
    d["obs2"] = um(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'ocupado', 0.88) RETURNING id", d["captura"], d["posicao2"], d["plan2"], d["metodo"])
    d["imp"] = um(conn, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas) VALUES (%s, 'estoque', repeat('b', 64), '2026-10-20', 2, 0) RETURNING id", d["loja"])
    d["snap"] = um(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, '2026-10-20', 38) RETURNING id", d["imp"], d["produto"])
    d["snap2"] = um(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, '2026-10-20', 0) RETURNING id", d["imp"], d["produto2"])
    d["alerta"] = um(conn, "INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra) VALUES (%s, %s, %s, 'D1', %s, %s, 'r1') RETURNING id", d["loja"], d["posicao"], d["produto"], d["obs"], d["snap"])
    conn.execute("RESET ROLE")
    return d


# ============================================================================ fixtures da aplicação
import datetime as _dt
import io as _io
import uuid as _uuid
from argon2 import PasswordHasher as _PH

SENHA = "senha-de-teste-123"
_HASH = _PH().hash(SENHA)


def _novo_ambiente(nome="Loja Teste", origem="sintetico"):
    """Cria (com commit) uma loja completa e devolve os ids. Limpeza: excluir_loja."""
    sufixo = _uuid.uuid4().hex[:6]
    d = {"sufixo": sufixo}
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        d["loja"] = um(c, "INSERT INTO op.loja (nome, origem_dado) VALUES (%s, %s) RETURNING id", nome, origem)
        for papel in ("repositor", "gerente", "analista", "admin"):
            uid = um(c, "INSERT INTO op.usuario (loja_id, nome, papel, login) VALUES (%s, %s, %s, %s) RETURNING id",
                     d["loja"], papel, papel, f"{papel}-{sufixo}")
            c.execute("INSERT INTO op.credencial (usuario_id, senha_hash) VALUES (%s, %s)", (uid, _HASH))
            d[papel] = uid
        d["produtos"], d["posicoes"] = [], []
        for i in range(3):
            p = um(c, "INSERT INTO op.produto (loja_id, descricao) VALUES (%s, %s) RETURNING id", d["loja"], f"prod{i}")
            c.execute("INSERT INTO op.codigo_externo (loja_id, codigo, produto_id) VALUES (%s, %s, %s)", (d["loja"], f"C{i}", p))
            pos = um(c, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, %s) RETURNING id", d["loja"], f"G1-N1-P{i}")
            c.execute("INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL))", (pos, p))
            d["produtos"].append(p); d["posicoes"].append(pos)
    return d


def _remover_ambiente(d):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        c.execute("CALL op.excluir_loja(%s)", (d["loja"],))


@pytest.fixture
def amb():
    d = _novo_ambiente()
    yield d
    _remover_ambiente(d)


@pytest.fixture
def amb_b():
    d = _novo_ambiente("Loja B")
    yield d
    _remover_ambiente(d)


@pytest.fixture
def cfg(tmp_path):
    from app.config import Config
    c = Config(dsn_app=DSN_APP, dsn_worker=DSN_WORKER, dir_dados=tmp_path / "dados",
               origem_permitida="http://testserver", cookie_seguro=False,
               max_upload_bytes=2 * 1024 * 1024, reserva_disco_bytes=1024)
    c.preparar_diretorios()
    return c


@pytest.fixture
def cliente(cfg):
    from fastapi.testclient import TestClient
    from app.api import criar_app
    with TestClient(criar_app(cfg)) as tc:
        yield tc


def entrar(tc, amb, papel):
    tc.cookies.clear()
    r = tc.post("/sessao", json={"login": f"{papel}-{amb['sufixo']}", "senha": SENHA})
    assert r.status_code == 200, r.text
    return tc


def jpeg(w=320, h=240, cor=(200, 30, 30), gps=False) -> bytes:
    from PIL import Image
    im = Image.new("RGB", (w, h), cor)
    buf = _io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x010F] = "FabricanteTeste"
        exif[0x8825] = {1: "S", 2: (2.0, 31.0, 0.0), 3: "W", 4: (44.0, 18.0, 0.0)}   # GPSInfo
        im.save(buf, "JPEG", exif=exif)
    else:
        im.save(buf, "JPEG")
    return buf.getvalue()


def png(w=320, h=240) -> bytes:
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 10)).save(buf, "PNG")
    return buf.getvalue()


def agora_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


@pytest.fixture
def metodo_registrado():
    nome, versao = "teste", _uuid.uuid4().hex[:8]
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        mid = um(c, "INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES (%s, %s, 0.8) RETURNING id", nome, versao)
    yield nome, versao
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        c.execute("DELETE FROM op.metodo_ocupacao WHERE id = %s AND NOT EXISTS (SELECT 1 FROM op.observacao_visual WHERE metodo_id = %s)", (mid, mid))


def arquivos(d):
    """Só arquivos (as áreas têm os subdiretórios .tmp e .lixeira)."""
    return [p for p in d.iterdir() if p.is_file()]
