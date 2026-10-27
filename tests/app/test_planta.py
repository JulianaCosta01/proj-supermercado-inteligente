"""Planta 2D: estados derivados no servidor, integração com o processamento real (B1) e interface no navegador."""
import datetime as dt
import pathlib
import threading
import time

import psycopg
import pytest
from conftest import DSN, SENHA, entrar
from sintetico import renderizar_face, fotografar
from test_worker_visao import face_cadastrada, worker_b1, enviar, importar_estoque, VERDADE  # noqa: F401  (fixtures)
from app.planta import estado_visual

AGORA = dt.datetime(2026, 10, 27, 15, 0, tzinfo=dt.timezone.utc)
RECENTE = AGORA - dt.timedelta(hours=1)
VELHA = AGORA - dt.timedelta(hours=10)


@pytest.mark.parametrize("args,esperado", [
    (("vazio", RECENTE, "D1", "aberto", "alinhada", 10), "possivel_divergencia"),
    (("vazio", RECENTE, "D1", "em_verificacao", "alinhada", 10), "possivel_divergencia"),
    (("vazio", RECENTE, "D1", "confirmado", "alinhada", 10), "divergencia_confirmada"),
    (("ocupado", RECENTE, "D2", "aberto", "alinhada", 0), "possivel_divergencia"),
    (("indeterminado", RECENTE, "V", "aberto", "alinhada", 10), "inconclusivo"),
    (("indeterminado", RECENTE, None, None, "desfocada", 10), "inconclusivo"),
    (("ocupado", RECENTE, None, None, "desfocada", 10), "inconclusivo"),        # foto inadequada nunca vira "ok"
    (("vazio", RECENTE, None, None, "alinhada", 0), "vazio_coerente"),
    (("vazio", RECENTE, None, None, "alinhada", None), "inconclusivo"),         # vazio sem estoque conhecido
    (("vazio", RECENTE, None, None, "alinhada", 1), "inconclusivo"),            # abaixo da margem
    (("parcial", RECENTE, None, None, "alinhada", 10), "parcial"),
    (("ocupado", RECENTE, None, None, "alinhada", 10), "ok"),
    (("ocupado", VELHA, None, None, "alinhada", 10), "sem_observacao"),
    ((None, None, None, None, None, None), "sem_observacao"),
])
def test_estado_visual(args, esperado):
    assert estado_visual(*args, agora=AGORA) == esperado


def _planta(cliente):
    r = cliente.get("/planta")
    assert r.status_code == 200
    return r.json()


def test_planta_reflete_o_processamento_real(cliente, amb, cfg, face_cadastrada, worker_b1):
    importar_estoque(cliente, amb)
    f = face_cadastrada
    enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=5), perspectiva=0.1, semente=3))
    assert worker_b1.processar_um() == "processada"
    entrar(cliente, amb, "gerente")
    dados = _planta(cliente)
    assert dados["sintetico"] is True
    face = next(x for x in dados["faces"] if x["id"] == f["face"])
    assert face["ultima_foto"]["situacao"] == "alinhada"
    estados = [p["estado"] for p in face["posicoes"]]
    assert estados == ["ok", "possivel_divergencia", "parcial", "ok", "possivel_divergencia", "ok", "ok", "parcial"]
    p1 = face["posicoes"][1]
    assert p1["produto"]["descricao"] and p1["estoque"]["quantidade"] == 10 and p1["alerta"]["tipo"] == "D1"

    # o gerente confirma: o estado passa a "divergência confirmada"
    r = cliente.post(f"/alertas/{p1['alerta']['id']}/transicoes", json={"versao": 1, "para": "confirmado", "causa": "reposicao_pendente"})
    assert r.status_code == 200
    face = next(x for x in _planta(cliente)["faces"] if x["id"] == f["face"])
    assert face["posicoes"][1]["estado"] == "divergencia_confirmada"
    assert face["posicoes"][4]["estado"] == "possivel_divergencia"

    # foto tremida depois: as posições sem alerta ficam inconclusivas, nunca "ok"
    entrar(cliente, amb, "repositor")
    enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=6), desfoque=7, semente=4))
    assert worker_b1.processar_um() == "processada"
    entrar(cliente, amb, "gerente")
    face = next(x for x in _planta(cliente)["faces"] if x["id"] == f["face"])
    assert face["ultima_foto"]["situacao"] == "desfocada"
    assert [p["estado"] for p in face["posicoes"]] == [
        "inconclusivo", "divergencia_confirmada", "inconclusivo", "inconclusivo", "possivel_divergencia",
        "inconclusivo", "inconclusivo", "inconclusivo"]


def test_imagem_da_captura_so_para_a_propria_loja(cliente, amb, amb_b, cfg, face_cadastrada, worker_b1):
    f = face_cadastrada
    cid = enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=5), semente=3))
    worker_b1.processar_um()
    entrar(cliente, amb, "gerente")
    r = cliente.get(f"/capturas/{cid}/imagem")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    entrar(cliente, amb_b, "gerente")
    assert cliente.get(f"/capturas/{cid}/imagem").status_code == 404
    assert all(x["id"] != f["face"] for x in _planta(cliente)["faces"])


def test_interface_tem_csp_sem_inline(cliente):
    r = cliente.get("/ui/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "<script>" not in r.text and "style=" not in r.text


# ------------------------------------------------------------------ navegador real (Chromium)
@pytest.fixture
def servidor(cfg):
    import uvicorn
    from dataclasses import replace
    from app.api import criar_app
    porta = 8765
    app = criar_app(replace(cfg, origem_permitida=f"http://127.0.0.1:{porta}"))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True; t.join(5)


def test_navegador_mostra_estados_e_confirma(servidor, cliente, amb, cfg, face_cadastrada, worker_b1):
    pw = pytest.importorskip("playwright.sync_api")
    importar_estoque(cliente, amb)
    f = face_cadastrada
    enviar(cliente, amb, fotografar(renderizar_face(VERDADE, f["pos"], f["mk"], semente=5), perspectiva=0.1, semente=3))
    worker_b1.processar_um()
    erros_csp = []
    with pw.sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1280, "height": 860})
        pag.on("console", lambda m: erros_csp.append(m.text) if "Content Security Policy" in m.text else None)
        pag.goto(f"{servidor}/ui/")
        pag.fill("input[name=login]", f"gerente-{amb['sufixo']}")
        pag.fill("input[name=senha]", SENHA)
        pag.click("button[type=submit]")
        pag.wait_for_selector(".posicao")
        rotulos = pag.eval_on_selector_all(".posicao", "els => els.map(e => e.getAttribute('aria-label'))")
        assert len(rotulos) == 8
        assert sum("Possível divergência" in r for r in rotulos) == 2 and not any("ruptura" in r.lower() for r in rotulos)
        assert pag.is_visible("#aviso-sintetico")
        pag.locator(".posicao").nth(1).click()
        assert "Possível divergência" in pag.inner_text("#painel")
        pasta = pathlib.Path(__file__).resolve().parents[2] / "docs" / "evidencias"
        pag.screenshot(path=str(pasta / "planta_possivel_divergencia.png"), full_page=True)
        pag.select_option("select[name=causa]", "reposicao_pendente")
        pag.click("button[data-acao=confirmado]")
        pag.locator("#painel").get_by_text("Divergência confirmada").first.wait_for()   # (a CSP bloqueia eval, por isso sem wait_for_function)
        pag.screenshot(path=str(pasta / "planta_confirmada.png"), full_page=True)
        nav.close()
    assert erros_csp == []
