"""API: sessão, autorização, upload com quarentena. Executa contra o banco real (papel stp_app)."""
import os
import stat
import psycopg
import pytest
from conftest import arquivos, DSN, entrar, jpeg, png, agora_iso, um

DESPROTEGIDAS = {("GET", "/saude"), ("POST", "/sessao"), ("GET", "/openapi.json")}


def test_login_e_senha_errada(cliente, amb):
    entrar(cliente, amb, "gerente")
    cliente.cookies.clear()
    assert cliente.post("/sessao", json={"login": f"gerente-{amb['sufixo']}", "senha": "errada"}).status_code == 401
    assert cliente.post("/sessao", json={"login": "naoexiste", "senha": "x"}).status_code == 401
    assert cliente.post("/sessao", json={"login": "abc", "senha": "x", "extra": 1}).status_code == 422


def test_desativar_usuario_derruba_sessao(cliente, amb):
    entrar(cliente, amb, "gerente")
    assert cliente.get("/alertas").status_code == 200
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        c.execute("UPDATE op.usuario SET ativo = false WHERE id = %s", (amb["gerente"],))
        assert um(c, "SELECT count(*) FROM op.sessao WHERE usuario_id = %s", amb["gerente"]) == 0   # gatilho
    assert cliente.get("/alertas").status_code == 401                                               # e a API


def test_logout_revoga(cliente, amb):
    entrar(cliente, amb, "gerente")
    cookie = cliente.cookies.get("stp_sessao")
    assert cliente.delete("/sessao").status_code == 200
    cliente.cookies.set("stp_sessao", cookie)
    assert cliente.get("/alertas").status_code == 401


def test_toda_rota_exige_sessao(cliente):
    """Lista as rotas da OpenAPI: qualquer rota nova sem proteção quebra este teste."""
    cliente.cookies.clear()
    spec = cliente.get("/openapi.json").json()
    verificadas = 0
    for caminho, metodos in spec["paths"].items():
        for metodo in metodos:
            if (metodo.upper(), caminho) in DESPROTEGIDAS:
                continue
            url = caminho.replace("{captura_id}", "1").replace("{alerta_id}", "1").replace("{tid}", "1")
            r = cliente.request(metodo.upper(), url, params={"capturado_em": agora_iso()},
                                json={"versao": 1, "para": "em_verificacao"})
            assert r.status_code == 401, (metodo, caminho, r.status_code)
            verificadas += 1
    assert verificadas >= 7


@pytest.mark.parametrize("papel,metodo,url,esperado", [
    ("analista", "POST", "/capturas", 403),
    ("repositor", "POST", "/importacoes/estoque", 403),
    ("gerente", "POST", "/importacoes/estoque", 403),
    ("repositor", "GET", "/importacoes/tentativas/1/relatorio.csv", 403),
])
def test_matriz_de_papeis(cliente, amb, papel, metodo, url, esperado):
    entrar(cliente, amb, papel)
    r = cliente.request(metodo, url, params={"capturado_em": agora_iso()}, content=jpeg())
    assert r.status_code == esperado


def test_isolamento_entre_lojas(cliente, amb, amb_b):
    entrar(cliente, amb, "repositor")
    cid = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg()).json()["id"]
    entrar(cliente, amb_b, "admin")
    assert cliente.get(f"/capturas/{cid}").status_code == 404
    with psycopg.connect(DSN, autocommit=True) as c:          # alerta na loja A
        c.execute("SET ROLE stp_migrator")
        cap = um(c, "INSERT INTO op.captura (loja_id, sha256_original, capturado_em) VALUES (%s, repeat('e', 64), now()) RETURNING id", amb["loja"])
        mid = um(c, "INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES ('iso', %s, 0.5) RETURNING id", amb["sufixo"])
        pv = um(c, "SELECT id FROM op.planograma_vigencia WHERE posicao_id = %s", amb["posicoes"][0])
        ob = um(c, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'indeterminado', 0.2) RETURNING id",
                cap, amb["posicoes"][0], pv, mid)
        al = um(c, "INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, versao_regra) VALUES (%s, %s, %s, 'V', %s, 'r2') RETURNING id",
                amb["loja"], amb["posicoes"][0], amb["produtos"][0], ob)
    assert all(a["id"] != al for a in cliente.get("/alertas").json())
    assert cliente.post(f"/alertas/{al}/transicoes", json={"versao": 1, "para": "em_verificacao"}).status_code == 403
    entrar(cliente, amb, "repositor")
    assert cliente.post(f"/alertas/{al}/transicoes", json={"versao": 1, "para": "confirmado", "causa": "outra"}).status_code == 403
    entrar(cliente, amb, "gerente")
    assert cliente.post(f"/alertas/{al}/transicoes", json={"versao": 1, "para": "em_verificacao"}).status_code == 200
    assert cliente.post(f"/alertas/{al}/transicoes", json={"versao": 1, "para": "rejeitado", "motivo": "outro"}).status_code == 409


def test_origem_diferente_recusada(cliente, amb):
    entrar(cliente, amb, "repositor")
    r = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cabecalhos_de_seguranca(cliente):
    h = cliente.get("/saude").headers
    assert h["x-content-type-options"] == "nosniff" and "default-src 'none'" in h["content-security-policy"]


def test_upload_vai_para_quarentena(cliente, amb, cfg):
    entrar(cliente, amb, "repositor")
    r = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg())
    assert r.status_code == 201 and r.json()["estado"] == "recebida"
    na_quarentena = arquivos(cfg.quarentena)
    assert len(na_quarentena) == 1 and "." not in na_quarentena[0].name            # UUID, sem extensão
    assert stat.S_IMODE(na_quarentena[0].stat().st_mode) == 0o600
    assert [p for p in cfg.imagens.rglob("*") if p.is_file()] == [] and arquivos(cfg.tmp_quarentena) + arquivos(cfg.tmp_imagens) == []


def test_upload_idempotente(cliente, amb, cfg):
    entrar(cliente, amb, "repositor")
    foto = png()
    a = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=foto)
    b = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=foto)
    assert a.status_code == 201 and b.status_code == 200
    assert b.json()["duplicada"] and a.json()["id"] == b.json()["id"]
    assert len(arquivos(cfg.quarentena)) == 1


@pytest.mark.parametrize("nome,conteudo,esperado", [
    ("svg renomeado", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 415),
    ("html", b"<!doctype html><script>alert(1)</script>", 415),
    ("gif", b"GIF89a" + b"\x00" * 100, 415),
    ("heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100, 415),
    ("vazio", b"", 400),
    ("curto", b"\xff\xd8", 415),
])
def test_upload_recusa_formato(cliente, amb, cfg, nome, conteudo, esperado):
    entrar(cliente, amb, "repositor")
    r = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=conteudo)
    assert r.status_code == esperado, nome
    assert arquivos(cfg.quarentena) == [] and arquivos(cfg.tmp_quarentena) + arquivos(cfg.tmp_imagens) == []


def test_upload_grande_demais(cliente, amb, cfg):
    entrar(cliente, amb, "repositor")
    grande = b"\xff\xd8\xff" + os.urandom(cfg.max_upload_bytes)
    assert cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=grande).status_code == 413
    assert arquivos(cfg.quarentena) == [] and arquivos(cfg.tmp_quarentena) + arquivos(cfg.tmp_imagens) == []


@pytest.mark.parametrize("quando", ["2026-10-20T10:00:00", "2099-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"])
def test_upload_data_invalida(cliente, amb, quando):
    entrar(cliente, amb, "repositor")
    assert cliente.post("/capturas", params={"capturado_em": quando}, content=jpeg()).status_code == 422


def test_falha_no_banco_nao_deixa_arquivo(cliente, amb, cfg):
    """Arquivo gravado, INSERT falha: a API responde 503 e remove o arquivo da quarentena."""
    entrar(cliente, amb, "repositor")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("REVOKE INSERT ON op.captura FROM stp_app")
    try:
        r = cliente.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg())
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("GRANT INSERT ON op.captura TO stp_app")
    assert r.status_code == 503 and "ref" in r.json()["detail"]
    assert arquivos(cfg.quarentena) == []


def test_disco_sem_reserva(amb, cfg, tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from app.api import criar_app
    cfg2 = replace(cfg, reserva_disco_bytes=10 ** 18)
    with TestClient(criar_app(cfg2)) as tc:
        entrar(tc, amb, "repositor")
        assert tc.post("/capturas", params={"capturado_em": agora_iso()}, content=jpeg()).status_code == 507
    assert arquivos(cfg.quarentena) == []
