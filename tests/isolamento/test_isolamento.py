"""Isolamento real do worker em contêiner Docker, contra um PostgreSQL com o pg_hba endurecido do repositório.

Executar à parte (precisa de Docker e de root):  python -m pytest -q tests/isolamento -m isolamento
Com STP_IMAGEM=<imagem construída pelo Dockerfile>, usa a imagem; sem ela, usa um contêiner vazio
com o Python do host montado somente para leitura (testa os mesmos controles de isolamento,
mas não o Dockerfile).
"""
import json
import os
import pathlib
import shutil
import subprocess
import textwrap

import psycopg
import pytest

pytestmark = pytest.mark.isolamento
RAIZ = pathlib.Path(__file__).resolve().parents[2]
PGBIN = pathlib.Path("/usr/lib/postgresql/16/bin")
BASE = pathlib.Path("/tmp/stp-isolamento")
SOCK = BASE / "sock"
PORTA = "5434"
SENHA = {"stp_worker_u": "worker-iso-teste", "stp_migrador_u": "migrador-iso-teste"}
UID = 10001


def _sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def _docker_ok():
    return shutil.which("docker") and os.geteuid() == 0 and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def banco_endurecido():
    if not _docker_ok():
        pytest.skip("Docker indisponível ou sem root")
    if BASE.exists():
        subprocess.run(["su", "postgres", "-c", f"{PGBIN}/pg_ctl -D {BASE}/data stop -m immediate"], capture_output=True)
        shutil.rmtree(BASE)
    SOCK.mkdir(parents=True); shutil.chown(BASE, "postgres"); shutil.chown(SOCK, "postgres")
    _sh(["su", "postgres", "-c", f"{PGBIN}/initdb -D {BASE}/data -A reject --auth-local=peer -U postgres"])
    shutil.copy(RAIZ / "db" / "pg_hba.conf", BASE / "data" / "pg_hba.conf")
    shutil.chown(BASE / "data" / "pg_hba.conf", "postgres")
    _sh(["su", "postgres", "-c", f"{PGBIN}/pg_ctl -D {BASE}/data -o \"-k {SOCK} -p {PORTA} -c listen_addresses=''\" -l {BASE}/log -w start"])
    os.chmod(SOCK, 0o755)

    def psql(sql):
        _sh(["su", "postgres", "-c", f"psql -h {SOCK} -p {PORTA} -v ON_ERROR_STOP=1 -d postgres -f -"], input=sql)

    def psql_stp(sql):
        _sh(["su", "postgres", "-c", f"psql -h {SOCK} -p {PORTA} -v ON_ERROR_STOP=1 -d stp -f -"], input=sql)

    psql("CREATE DATABASE stp;")
    psql_stp((RAIZ / "db" / "roles.sql").read_text().replace("{db}", "stp"))
    psql_stp(f"""CREATE ROLE stp_worker_u LOGIN PASSWORD '{SENHA["stp_worker_u"]}' IN ROLE stp_worker;
                 CREATE ROLE stp_migrador_u LOGIN PASSWORD '{SENHA["stp_migrador_u"]}' IN ROLE stp_migrator;
                 GRANT CREATE ON DATABASE stp TO stp_migrador_u;""")
    env = dict(os.environ, STP_MIGRATION_URL=f"postgresql+psycopg://stp_migrador_u:{SENHA['stp_migrador_u']}@/stp?host={SOCK}&port={PORTA}")
    _sh(["python3", "-m", "alembic", "upgrade", "head"], cwd=RAIZ, env=env)
    yield f"host={SOCK} port={PORTA} dbname=stp"
    subprocess.run(["su", "postgres", "-c", f"{PGBIN}/pg_ctl -D {BASE}/data stop -m fast"], capture_output=True)


@pytest.fixture(scope="module")
def cenario(banco_endurecido, tmp_path_factory):
    """Loja sintética com face, referência e uma foto na fila; áreas de dados com dono uid 10001."""
    import sys
    sys.path.insert(0, str(RAIZ / "tests"))
    from sintetico import FACE, layout_marcadores, layout_posicoes, renderizar_face, fotografar
    from app.cadastro import cadastrar_face, registrar_referencia
    from app.config import Config
    dados = BASE / "dados"
    cfg = Config(dsn_worker="", dir_dados=dados)
    cfg.preparar_diretorios()
    dsn_mig = f"{banco_endurecido} user=stp_migrador_u password={SENHA['stp_migrador_u']}"
    mk, pos = layout_marcadores(), layout_posicoes()
    with psycopg.connect(dsn_mig) as c:
        c.execute("SET ROLE stp_migrator")
        loja = c.execute("INSERT INTO op.loja (nome, origem_dado) VALUES ('Isolamento', 'sintetico') RETURNING id").fetchone()[0]
        prod = c.execute("INSERT INTO op.produto (loja_id, descricao) VALUES (%s, 'p') RETURNING id", (loja,)).fetchone()[0]
        c.execute("RESET ROLE")
        fid = cadastrar_face(c, loja, dict(FACE, codigo="ISO", marcadores=mk, posicoes=[dict(p, produto_id=prod) for p in pos]))
        registrar_referencia(c, cfg, fid, fotografar(renderizar_face(["cheio"] * 8, pos, mk, semente=1), perspectiva=0.03, semente=1))
        foto = fotografar(renderizar_face(["cheio", "vazio"] * 4, pos, mk, semente=5), semente=2)
        chave = "0b6e1c2a-1111-4c3b-9a9a-000000000001"
        (cfg.quarentena / chave).write_bytes(foto)
        c.execute("SET ROLE stp_migrator")
        cap = c.execute("INSERT INTO op.captura (loja_id, sha256_original, quarentena_chave, capturado_em) VALUES (%s, repeat('f', 64), %s, now()) RETURNING id",
                        (loja, chave)).fetchone()[0]
        c.commit()
    for p in [dados, *dados.rglob("*")]:
        os.chown(p, UID, UID)
    pgpass = BASE / "pgpass_worker"
    pgpass.write_text(f"*:{PORTA}:stp:stp_worker_u:{SENHA['stp_worker_u']}\n")
    os.chown(pgpass, UID, UID); os.chmod(pgpass, 0o600)
    return {"cfg": cfg, "captura": cap, "chave": chave, "pgpass": pgpass, "dsn_mig": dsn_mig}


def _docker(cenario, script):
    imagem = os.environ.get("STP_IMAGEM")
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--user", f"{UID}:{UID}", "--memory", "1g", "--pids-limit", "128",
           "--tmpfs", "/tmp:size=64m",
           "-v", f"{SOCK}:/var/run/postgresql",
           "-v", f"{cenario['cfg'].quarentena}:/dados/quarentena",
           "-v", f"{cenario['cfg'].imagens}:/dados/imagens",
           "-v", f"{cenario['cfg'].referencias}:/dados/referencias:ro",
           "-v", f"{cenario['pgpass']}:/run/secrets/pgpass_worker:ro",
           "-e", f"STP_DSN_WORKER=host=/var/run/postgresql port={PORTA} dbname=stp user=stp_worker_u",
           "-e", "PGPASSFILE=/run/secrets/pgpass_worker", "-e", "STP_DIR_DADOS=/dados", "-e", "HOME=/tmp"]
    if imagem:
        cmd += [imagem]
    else:
        subprocess.run("tar cf - --files-from /dev/null | docker import - stp-vazio:1", shell=True, check=True, capture_output=True)
        cmd += ["-v", "/usr:/usr:ro", "-v", "/lib:/lib:ro", "-v", "/lib64:/lib64:ro", "-v", "/etc/ld.so.cache:/etc/ld.so.cache:ro",
                "-v", f"{RAIZ}:/srv/stp:ro", "-w", "/srv/stp", "stp-vazio:1"]
    py = "python3" if not imagem else "python"
    r = subprocess.run(cmd + [py if not imagem else "python", "-c", textwrap.dedent(script)], capture_output=True, text=True, timeout=180)
    return r


SONDA = """
import json, os, socket, psycopg
res = {}
def tenta(nome, f):
    try:
        f(); res[nome] = "permitido"
    except Exception as e:
        res[nome] = "bloqueado:" + type(e).__name__
tenta("internet_tcp", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
tenta("dns", lambda: socket.getaddrinfo("pypi.org", 443))
tenta("escrever_raiz", lambda: open("/x", "w"))
tenta("escrever_codigo", lambda: open("/srv/stp/app/x.py" if os.path.isdir("/srv/stp/app") else "/usr/x", "w"))
tenta("escrever_referencias", lambda: open("/dados/referencias/x", "w"))
tenta("escrever_imagens_tmp", lambda: open("/dados/imagens/.tmp/sonda", "w").close())
tenta("banco_superusuario", lambda: psycopg.connect("host=/var/run/postgresql port=5434 dbname=stp user=postgres", connect_timeout=3))
tenta("banco_worker_sem_senha", lambda: psycopg.connect("host=/var/run/postgresql port=5434 dbname=stp user=stp_worker_u passfile=/dev/null", connect_timeout=3))
tenta("banco_worker_com_senha", lambda: psycopg.connect(os.environ["STP_DSN_WORKER"]).close())
tenta("banco_migrador", lambda: psycopg.connect("host=/var/run/postgresql port=5434 dbname=stp user=stp_migrador_u passfile=/dev/null", connect_timeout=3))
raiz = [l.split() for l in open("/proc/mounts") if l.split()[1] == "/"][-1]
res["raiz_somente_leitura"] = "ro" in raiz[3].split(",")
st = open("/proc/self/status").read()
res["uid"] = os.getuid()
res["CapEff"] = st.split("CapEff:")[1].split()[0]
res["NoNewPrivs"] = st.split("NoNewPrivs:")[1].split()[0]
print(json.dumps(res))
"""

PROCESSAR = """
import json, pathlib
from app.config import Config
from app.visao import MetodoB1
from app.worker import Worker
cfg = Config()
w = Worker(cfg, MetodoB1(cfg.referencias))
print(json.dumps({"resultado": w.processar_um()}))
"""


def test_controles_de_isolamento(cenario):
    r = _docker(cenario, SONDA)
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    if os.environ.get("STP_EVIDENCIAS"):
        pathlib.Path(os.environ["STP_EVIDENCIAS"], "isolamento_worker.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    for chave in ("internet_tcp", "dns", "escrever_raiz", "escrever_codigo", "escrever_referencias",
                  "banco_superusuario", "banco_worker_sem_senha", "banco_migrador"):
        assert res[chave].startswith("bloqueado"), (chave, res[chave])
    assert res["escrever_imagens_tmp"] == "permitido"
    assert res["banco_worker_com_senha"] == "permitido"
    assert res["raiz_somente_leitura"] is True
    assert res["uid"] == UID and res["CapEff"] == "0000000000000000" and res["NoNewPrivs"] == "1"


def test_worker_processa_dentro_do_conteiner(cenario):
    r = _docker(cenario, PROCESSAR)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1])["resultado"] == "processada"
    with psycopg.connect(cenario["dsn_mig"]) as c:
        estado, chave = c.execute("SELECT estado, imagem_chave FROM op.captura WHERE id = %s", (cenario["captura"],)).fetchone()
        classes = [x[0] for x in c.execute("SELECT classe FROM op.observacao_visual o JOIN op.posicao p ON p.id = o.posicao_id WHERE o.captura_id = %s ORDER BY p.y_mm, p.x_mm", (cenario["captura"],))]
    assert estado == "processada" and (cenario["cfg"].imagens / chave).is_file()
    assert classes == ["ocupado", "vazio"] * 4
    assert not (cenario["cfg"].quarentena / cenario["chave"]).exists()
