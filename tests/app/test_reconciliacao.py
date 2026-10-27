"""Reconciliação: nunca apaga em massa, nunca apaga sem consultar o banco, tudo passa pela lixeira."""
import datetime as dt
import os
import time
import uuid
import psycopg
import pytest
from conftest import arquivos, DSN, DSN_WORKER, entrar, jpeg, agora_iso, um
from app.reconciliacao import reconciliar


def envelhecer(p, horas=48):
    t = time.time() - horas * 3600
    os.utime(p, (t, t))


def orfao_imagem(cfg, horas=48):
    h = uuid.uuid4().hex + uuid.uuid4().hex
    p = cfg.imagens / h[:2] / f"{h}.jpg"
    p.parent.mkdir(exist_ok=True); p.write_bytes(b"x")
    envelhecer(p, horas)
    return p


def test_banco_fora_do_ar_nao_apaga_nada(cfg):
    for _ in range(3):
        orfao_imagem(cfg)
    r = reconciliar(cfg, "host=/tmp/inexistente port=1 dbname=x user=x connect_timeout=1")
    assert r.abortada and "banco" in r.abortada
    assert len(list(cfg.imagens.rglob("*.jpg"))) == 3 and not any(cfg.lixeira_de(cfg.imagens).rglob('*.jpg'))


def test_banco_vazio_com_arquivos_aborta(cfg):
    with psycopg.connect(DSN) as c:
        if um(c, "SELECT count(*) FROM op.captura") != 0:
            pytest.skip("há capturas de outros testes no banco")
    orfao_imagem(cfg)
    r = reconciliar(cfg, DSN_WORKER)
    assert r.abortada and "restauração" in r.abortada


@pytest.fixture
def uma_captura(cliente, amb):
    """Garante ao menos uma captura no banco (o banco 'não vazio')."""
    entrar(cliente, amb, "repositor")
    return cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg()).json()["id"]


def test_trava_contra_exclusao_em_massa(cfg, uma_captura):
    for _ in range(30):
        orfao_imagem(cfg)
    r = reconciliar(cfg, DSN_WORKER)
    assert r.abortada and "trava" in r.abortada
    assert len(list(cfg.imagens.rglob("*.jpg"))) == 30


def test_orfao_antigo_vai_para_lixeira_e_novo_fica(cfg, uma_captura):
    velho, novo = orfao_imagem(cfg, 48), orfao_imagem(cfg, 1)
    r = reconciliar(cfg, DSN_WORKER)
    assert r.abortada is None
    assert not velho.exists() and novo.exists()
    assert len(r.movidos_lixeira) == 1 and len(list(cfg.lixeira_de(cfg.imagens).rglob("*.jpg"))) == 1


def test_quarentena_em_processamento_fica_e_finalizada_sai(cliente, amb, cfg):
    entrar(cliente, amb, "repositor")
    a = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg(cor=(1, 1, 1))).json()["id"]
    b = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg(cor=(2, 2, 2))).json()["id"]
    with psycopg.connect(DSN) as c:
        qa, qb = (str(um(c, "SELECT quarentena_chave FROM op.captura WHERE id = %s", x)) for x in (a, b))
    for q in arquivos(cfg.quarentena):
        envelhecer(q)
    with psycopg.connect(DSN, autocommit=True) as c:      # simula: A processada, mas o arquivo não foi apagado
        c.execute("SET ROLE stp_migrator")
        c.execute("UPDATE op.captura SET estado = 'processada', imagem_sha256 = repeat('1', 64) WHERE id = %s", (a,))
        c.execute("UPDATE op.captura SET estado = 'em_processamento', reserva_token = gen_random_uuid(), reserva_expira_em = now() + interval '1 hour' WHERE id = %s", (b,))
    r = reconciliar(cfg, DSN_WORKER)
    assert r.abortada is None
    assert not (cfg.quarentena / qa).exists() and (cfg.quarentena / qb).exists()


def test_imagem_ausente_e_registrada(cfg, uma_captura):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        h = "ab" + "c" * 62
        c.execute("UPDATE op.captura SET estado = 'processada', imagem_sha256 = %s, imagem_chave = %s WHERE id = %s",
                  (h, f"ab/{h}.jpg", uma_captura))
    r = reconciliar(cfg, DSN_WORKER)
    assert r.imagens_ausentes == [uma_captura]
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT count(*) FROM op.imagem_ausente WHERE captura_id = %s", uma_captura) == 1
    reconciliar(cfg, DSN_WORKER)                         # idempotente
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT count(*) FROM op.imagem_ausente WHERE captura_id = %s", uma_captura) == 1


def test_lixeira_e_tmp_expiram(cfg, uma_captura):
    antiga = cfg.lixeira_de(cfg.imagens) / (dt.date.today() - dt.timedelta(days=8)).strftime("%Y%m%d")
    antiga.mkdir(); (antiga / "x").write_bytes(b"x")
    recente = cfg.lixeira_de(cfg.imagens) / dt.date.today().strftime("%Y%m%d")
    recente.mkdir(exist_ok=True); (recente / "y").write_bytes(b"y")
    t = cfg.tmp_imagens / "resto.part"; t.write_bytes(b"z"); envelhecer(t, 2)
    r = reconciliar(cfg, DSN_WORKER)
    assert not antiga.exists() and recente.exists() and not t.exists() and r.tmp_apagados == 1
