"""Worker real: upload pela API -> reserva -> recodificação isolada -> conclusão atômica."""
import datetime as dt
import io
import time
import psycopg
import pytest
from PIL import Image
from conftest import DSN, entrar, jpeg, png, agora_iso, um
from app.worker import Worker, recodificar_isolado


class MetodoTeste:
    """Substitui o método de visão (ainda não implementado). Classe por posição configurável."""
    def __init__(self, nome, versao, classes=None, falhar_vezes=0, durante=None):
        self.nome, self.versao, self.classes = nome, versao, classes or {}
        self.falhar_vezes, self.durante, self.chamadas = falhar_vezes, durante, 0

    def classificar(self, imagem_path, captura, posicoes):
        self.chamadas += 1
        if self.durante:
            self.durante(captura)
        if self.chamadas <= self.falhar_vezes:
            raise RuntimeError("falha simulada no meio do processamento")
        return [(p["posicao_id"], self.classes.get(p["posicao_id"], ("ocupado", 0.95))[0],
                 self.classes.get(p["posicao_id"], ("ocupado", 0.95))[1]) for p in posicoes]


def enviar(tc, conteudo):
    r = tc.post("/capturas", params={"capturado_em": agora_iso()}, content=conteudo)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def estado(cid):
    with psycopg.connect(DSN) as c:
        return c.execute("SELECT estado, motivo_recusa, imagem_chave, imagem_sha256, tentativas FROM op.captura WHERE id = %s", (cid,)).fetchone()


def importar(tc, amb, linhas):
    entrar(tc, amb, "analista")
    hoje = dt.date.today().isoformat()
    corpo = "codigo;ean;descricao;quantidade;data\n" + "".join(f"{c};;x;{q};{hoje}\n" for c, q in linhas)
    assert tc.post("/importacoes/estoque", content=corpo.encode()).status_code == 201


@pytest.fixture
def w(cfg, metodo_registrado):
    criados = []

    def fabricar(**kw):
        wk = Worker(cfg, MetodoTeste(*metodo_registrado, **kw))
        criados.append(wk)
        return wk
    yield fabricar
    for wk in criados:
        wk.fechar()


def test_fluxo_completo_remove_exif_e_quarentena(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, jpeg(gps=True))
    assert Image.open(io.BytesIO(jpeg(gps=True))).getexif().get(0x8825) is not None   # a entrada tem GPS
    assert w().processar_um() == "processada"
    est, _, chave, sha, _ = estado(cid)
    assert est == "processada"
    arquivo = cfg.imagens / chave
    import hashlib
    assert hashlib.sha256(arquivo.read_bytes()).hexdigest() == sha
    assert len(Image.open(arquivo).getexif()) == 0                      # nenhum metadado na saída
    assert list(cfg.quarentena.iterdir()) == [] and list(cfg.tmp.iterdir()) == []
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT count(*) FROM op.observacao_visual WHERE captura_id = %s", cid) == 3


def test_png_e_aceito_e_vira_jpeg(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, png())
    assert w().processar_um() == "processada"
    assert Image.open(cfg.imagens / estado(cid)[2]).format == "JPEG"


def _bomba_png():
    im = Image.new("1", (12000, 12000), 0)          # 144 MP, comprime para poucos KB
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@pytest.mark.parametrize("nome,fabricar", [
    ("jpeg truncado", lambda: jpeg()[:300]),
    ("cabeçalho jpeg + lixo", lambda: b"\xff\xd8\xff\xe0" + b"\x00garbage" * 200),
    ("imagem minúscula", lambda: jpeg(10, 10)),
    ("bomba de descompressão", _bomba_png),
])
def test_imagem_invalida_e_recusada_sem_nova_tentativa(cliente, amb, cfg, w, nome, fabricar):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, fabricar())
    inicio = time.time()
    assert w().processar_um() == "recusada", nome
    est, motivo, chave, _, tent = estado(cid)
    assert est == "recusada" and motivo.startswith("imagem_invalida") and chave is None and tent == 1
    assert list(cfg.quarentena.iterdir()) == []
    assert time.time() - inicio < 30


def test_poliglota_jpeg_html_sai_limpo(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, jpeg() + b"<html><script>alert(1)</script></html>")
    assert w().processar_um() == "processada"
    assert b"<script>" not in (cfg.imagens / estado(cid)[2]).read_bytes()


def test_tempo_limite_da_decodificacao(cfg, tmp_path):
    f = tmp_path / "x"; f.write_bytes(jpeg())
    assert recodificar_isolado(f, tmp_path / "y.jpg", tempo_limite=0.001)[0] == "falha"


def test_divergencia_gera_alerta_uma_vez(cliente, amb, cfg, w):
    importar(cliente, amb, [("C0", 10), ("C1", 0), ("C2", 1)])
    p0, p1, p2 = amb["posicoes"]
    wk = w(classes={p0: ("vazio", 0.95), p1: ("ocupado", 0.95), p2: ("vazio", 0.95)})
    entrar(cliente, amb, "repositor")
    enviar(cliente, jpeg(cor=(1, 2, 3)))
    assert wk.processar_um() == "processada"
    enviar(cliente, jpeg(cor=(4, 5, 6)))            # segunda ronda: mesma situação
    assert wk.processar_um() == "processada"        # antes da correção 0002, falhava aqui
    with psycopg.connect(DSN) as c:
        tipos = dict(c.execute("SELECT posicao_id, tipo FROM op.alerta WHERE loja_id = %s", (amb["loja"],)).fetchall())
        n = um(c, "SELECT count(*) FROM op.alerta WHERE loja_id = %s", amb["loja"])
    assert tipos == {p0: "D1", p1: "D2", p2: "V"} and n == 3    # p2: 1 unidade < margem -> inconclusivo


def test_estoque_velho_vira_inconclusivo(cliente, amb, cfg, w):
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_app")
        imp = um(c, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas) VALUES (%s, 'estoque', repeat('7', 64), current_date - 5, 1, 0) RETURNING id", amb["loja"])
        c.execute("INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, current_date - 5, 50)", (imp, amb["produtos"][0]))
    wk = w(classes={amb["posicoes"][0]: ("vazio", 0.99)})
    entrar(cliente, amb, "repositor")
    enviar(cliente, jpeg())
    assert wk.processar_um() == "processada"
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT tipo, snapshot_id FROM op.alerta WHERE posicao_id = %s", (amb["posicoes"][0],)).fetchone() == ("V", None)


def test_planograma_com_rejeicao_pendente_vira_inconclusivo(cliente, amb, cfg, w):
    importar(cliente, amb, [("C0", 10)])
    p0 = amb["posicoes"][0]
    wk = w(classes={p0: ("vazio", 0.95)})
    entrar(cliente, amb, "repositor"); enviar(cliente, jpeg(cor=(9, 9, 9)))
    wk.processar_um()
    entrar(cliente, amb, "gerente")
    al = cliente.get("/alertas").json()[0]
    assert al["tipo"] == "D1"
    assert cliente.post(f"/alertas/{al['id']}/transicoes", json={"versao": 1, "para": "rejeitado", "motivo": "planograma_errado"}).status_code == 200
    entrar(cliente, amb, "repositor"); enviar(cliente, jpeg(cor=(8, 8, 8)))
    wk.processar_um()
    abertos = [a for a in cliente.get("/alertas", params={"status": "aberto"}).json() if a["posicao_id"] == p0]
    assert [a["tipo"] for a in abertos] == ["V"]


def test_nenhuma_transacao_aberta_durante_processamento(cliente, amb, cfg, w):
    vistos = {}

    def sondar(captura):
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("SELECT 1 FROM op.captura WHERE id = %s FOR UPDATE NOWAIT", (captura["id"],))   # não bloqueia
            vistos["em_transacao"] = um(c, """SELECT count(*) FROM pg_stat_activity
                                               WHERE usename = 'stp_worker_u' AND state LIKE 'idle in transaction%%'""")
    wk = w(durante=sondar)
    entrar(cliente, amb, "repositor"); enviar(cliente, jpeg())
    assert wk.processar_um() == "processada"
    assert vistos["em_transacao"] == 0


def test_queda_no_meio_e_retomada_sem_duplicar(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, jpeg())
    w1 = w(falhar_vezes=1)
    with pytest.raises(RuntimeError):
        w1.processar_um(lease="1 second")           # "processo morre" depois de reservar
    assert estado(cid)[0] == "em_processamento"
    time.sleep(1.2)
    w2 = w()
    assert w2.processar_um() == "processada"
    est, _, _, _, tent = estado(cid)
    assert est == "processada" and tent == 2
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT count(*) FROM op.observacao_visual WHERE captura_id = %s", cid) == 3
    assert list(cfg.quarentena.iterdir()) == []


def test_worker_atrasado_e_descartado(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, jpeg())
    w2 = w()

    def dormir(_):
        time.sleep(1.3)
        assert w2.processar_um() == "processada"     # outro worker pega a reserva vencida
    w1 = w(durante=dormir)
    assert w1.processar_um(lease="1 second") == "descartada"
    with psycopg.connect(DSN) as c:
        assert um(c, "SELECT count(*) FROM op.observacao_visual WHERE captura_id = %s", cid) == 3


def test_arquivo_sumiu_da_quarentena(cliente, amb, cfg, w):
    entrar(cliente, amb, "repositor")
    cid = enviar(cliente, jpeg())
    for f in cfg.quarentena.iterdir():
        f.unlink()
    assert w().processar_um() == "recusada"
    assert estado(cid)[1] == "arquivo_ausente"
