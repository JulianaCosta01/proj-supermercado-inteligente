"""Fluxo completo com o método de visão real (B1) e fotos SINTÉTICAS:
cadastro da face -> referência -> upload pela API -> worker -> observações, alertas e alinhamento."""
import datetime as dt
import psycopg
import pytest
from conftest import DSN, entrar, agora_iso, um
from sintetico import FACE, layout_marcadores, layout_posicoes, renderizar_face, fotografar
from app.cadastro import cadastrar_face, registrar_referencia
from app.visao import MetodoB1
from app.worker import Worker

VERDADE = ["cheio", "vazio", "parcial", "cheio", "vazio", "cheio", "cheio", "parcial"]


@pytest.fixture
def face_cadastrada(amb, cfg):
    mk, pos = layout_marcadores(base_id=hash(amb["sufixo"]) % 10 * 4), layout_posicoes()
    d = dict(FACE, codigo=f"G-{amb['sufixo']}", marcadores=mk,
             posicoes=[dict(p, produto_id=amb["produtos"][i % 3]) for i, p in enumerate(pos)])
    with psycopg.connect(DSN) as c:
        fid = cadastrar_face(c, amb["loja"], d)
        ref = fotografar(renderizar_face(["cheio"] * 8, pos, mk, semente=1), perspectiva=0.03, semente=1)
        registrar_referencia(c, cfg, fid, ref)
        c.commit()
        ids = [r[0] for r in c.execute("SELECT id FROM op.posicao WHERE face_id = %s ORDER BY id", (fid,))]
    return {"face": fid, "mk": mk, "pos": pos, "posicoes": ids}


@pytest.fixture
def worker_b1(cfg):
    w = Worker(cfg, MetodoB1(cfg.referencias))
    yield w
    w.fechar()


def enviar(tc, amb, foto):
    entrar(tc, amb, "repositor")
    r = tc.post("/capturas", params={"capturado_em": agora_iso()}, content=foto)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def importar_estoque(tc, amb, qtd=10):
    entrar(tc, amb, "analista")
    hoje = dt.date.today().isoformat()
    corpo = "codigo;ean;descricao;quantidade;data\n" + "".join(f"C{i};;x;{qtd};{hoje}\n" for i in range(3))
    assert tc.post("/importacoes/estoque", content=corpo.encode()).status_code == 201


def test_foto_boa_gera_observacoes_nas_posicoes_certas_e_alertas(cliente, amb, cfg, face_cadastrada, worker_b1):
    importar_estoque(cliente, amb)
    f = face_cadastrada
    cid = enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=5), perspectiva=0.12, semente=3))
    assert worker_b1.processar_um() == "processada"
    with psycopg.connect(DSN) as c:
        obs = dict(c.execute("SELECT posicao_id, classe FROM op.observacao_visual WHERE captura_id = %s", (cid,)).fetchall())
        alin = c.execute("SELECT face_id, situacao, marcadores_detectados FROM op.captura_face WHERE captura_id = %s", (cid,)).fetchone()
        alertas = dict(c.execute("SELECT posicao_id, tipo FROM op.alerta WHERE loja_id = %s", (amb["loja"],)).fetchall())
    p = f["posicoes"]
    assert alin == (f["face"], "alinhada", 4)
    assert [obs[x] for x in p] == ["ocupado", "vazio", "parcial", "ocupado", "vazio", "ocupado", "ocupado", "parcial"]
    assert alertas == {p[1]: "D1", p[4]: "D1"}          # vazio com estoque: possível divergência; parcial: nada


def test_foto_tremida_registra_situacao_sem_alertas(cliente, amb, cfg, face_cadastrada, worker_b1):
    importar_estoque(cliente, amb)
    f = face_cadastrada
    cid = enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=5), desfoque=7, semente=4))
    assert worker_b1.processar_um() == "processada"
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT situacao FROM op.captura_face WHERE captura_id = %s", cid) == "desfocada"
        assert {r[0] for r in c.execute("SELECT classe FROM op.observacao_visual WHERE captura_id = %s", (cid,))} == {"indeterminado"}
        assert um(c, "SELECT count(*) FROM op.alerta WHERE loja_id = %s", amb["loja"]) == 0


def test_foto_sem_marcadores_e_recusada(cliente, amb, cfg, face_cadastrada, worker_b1):
    f = face_cadastrada
    foto = fotografar(renderizar_face(VERDADE, f["pos"], f["mk"]), ocultar_marcadores=[m["id"] for m in f["mk"]], marcadores=f["mk"])
    cid = enviar(cliente, amb, foto)
    assert worker_b1.processar_um() == "recusada"
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT motivo_recusa FROM op.captura WHERE id = %s", cid) == "imagem_inadequada: marcadores_nao_encontrados"


def test_banco_recusa_observacao_de_outra_face(amb, face_cadastrada):
    """Defesa no banco: a conclusão com face informada não aceita posição de outra face."""
    with psycopg.connect(DSN) as c:
        c.execute("SET ROLE stp_worker")
        with pytest.raises(psycopg.Error) as e:
            c.execute("""SELECT op.concluir_captura(1, gen_random_uuid(), NULL, repeat('a', 64), 1,
                         jsonb_build_array(jsonb_build_object('posicao_id', %s::bigint, 'classe', 'vazio', 'confianca', 0.9)),
                         NULL, %s, 'alinhada', 4::smallint, 0.1, '{}')""", (amb["posicoes"][0], face_cadastrada["face"]))
        assert e.value.sqlstate in ("STP02", "STP10")


def test_marcador_repetido_entre_faces_e_recusado(amb):
    mk = layout_marcadores(base_id=90)
    d = dict(FACE, codigo="A1", marcadores=mk, posicoes=[])
    with psycopg.connect(DSN) as c:
        cadastrar_face(c, amb["loja"], d)
        with pytest.raises(psycopg.errors.UniqueViolation):
            cadastrar_face(c, amb["loja"], dict(d, codigo="A2"))
        c.rollback()


def test_posicao_fora_da_face_e_recusada(amb):
    d = dict(FACE, codigo="B1", marcadores=layout_marcadores(base_id=80),
             posicoes=[{"codigo": "X", "produto_id": amb["produtos"][0], "x_mm": 1100, "y_mm": 10, "largura_mm": 300, "altura_mm": 100}])
    with psycopg.connect(DSN) as c:
        with pytest.raises(psycopg.Error) as e:
            cadastrar_face(c, amb["loja"], d)
        assert e.value.sqlstate == "STP02"
        c.rollback()
