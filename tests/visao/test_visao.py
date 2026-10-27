"""Linha de base B1 com imagens SINTÉTICAS controladas (verdade conhecida).

Estes testes mostram o comportamento do método em condições controladas. Não são
validação em loja: fotos reais têm reflexos, embalagens e fundos que o gerador não cobre.
"""
import pathlib
import cv2
import numpy as np
import pytest
from sintetico import FACE, layout_marcadores, layout_posicoes, renderizar_face, fotografar
from app.metodos import ImagemInadequada
from app.visao import MetodoB1, gerar_referencia

FID = 7
VERDADE = ["cheio", "vazio", "parcial", "trocado", "vazio", "cheio", "cheio", "parcial"]
ESPERADO = ["ocupado", "vazio", "parcial", "parcial", "vazio", "ocupado", "ocupado", "parcial"]


def _montar(tmp, fundo="liso"):
    mk, pos = layout_marcadores(), layout_posicoes()
    face = dict(FACE, marcadores=mk)
    ref = fotografar(renderizar_face(["cheio"] * 8, pos, mk, semente=1, fundo=fundo), perspectiva=0.03, semente=1)
    png, sha = gerar_referencia(cv2.imdecode(np.frombuffer(ref, np.uint8), 1), face)
    (tmp / f"{sha}.png").write_bytes(png)
    face["referencia_sha256"] = sha
    posicoes = [dict(p, posicao_id=i, face_id=FID) for i, p in enumerate(pos)]
    return face, mk, pos, posicoes


@pytest.fixture
def cena(tmp_path):
    face, mk, pos, posicoes = _montar(tmp_path)
    metodo = MetodoB1(tmp_path)

    def rodar(verdade=VERDADE, semente_cena=5, faces=None, render_kw=None, **foto_kw):
        foto = fotografar(renderizar_face(verdade, pos, mk, semente=semente_cena, **(render_kw or {})), marcadores=mk, **foto_kw)
        f = tmp_path / "foto.jpg"; f.write_bytes(foto)
        return metodo.classificar(f, {"faces": faces or {FID: face}}, posicoes), metodo.ultimo_alinhamento
    return rodar, face, mk, posicoes, tmp_path


def classes(res):
    return [c for _, c, _ in res]


# ---------------------------------------------------------------- funciona
@pytest.mark.parametrize("nome,kw", [
    ("frontal", dict(perspectiva=0.02, semente=2)),
    ("perspectiva moderada", dict(perspectiva=0.12, semente=3)),
    ("perspectiva forte", dict(perspectiva=0.22, semente=9)),
    ("sombra lateral", dict(iluminacao=(0.45, 1.1), semente=4)),
    ("mais escuro", dict(iluminacao=0.6, semente=4)),
    ("mais claro", dict(iluminacao=1.15, semente=4)),
    ("tremido leve", dict(desfoque=5, semente=4)),
    ("um marcador oculto", dict(ocultar_marcadores=[2], semente=4)),
    ("compressão forte", dict(qualidade_jpeg=55, semente=6)),
])
def test_classifica_posicoes_corretas(cena, nome, kw):
    rodar, *_ = cena
    res, alin = rodar(**kw)
    assert alin.situacao == "alinhada", nome
    assert [p for p, _, _ in res] == list(range(8)), "cada resultado aponta para a posição certa"
    assert classes(res) == ESPERADO, nome


def test_vazio_tem_confianca_alta_e_parcial_nao(cena):
    rodar, *_ = cena
    res, _ = rodar(perspectiva=0.05, semente=2)
    conf = {p: c for p, _, c in res}
    assert conf[1] >= 0.8 and conf[4] >= 0.8          # vazios inequívocos
    assert conf[2] < 0.8 and conf[3] < 0.8            # parcial e produto trocado ficam abaixo do limiar


def test_resultado_reprodutivel(cena):
    rodar, *_ = cena
    assert rodar(perspectiva=0.1, semente=3)[0] == rodar(perspectiva=0.1, semente=3)[0]


# ---------------------------------------------------------------- falhas tratadas
@pytest.mark.parametrize("nome,kw,situacao", [
    ("dois marcadores ocultos", dict(ocultar_marcadores=[1, 2], semente=4), "marcadores_insuficientes"),
    ("face cortada no quadro", dict(deslocamento=(700, 0), semente=4), "marcadores_insuficientes"),
    ("muito longe", dict(escala=0.45, semente=4), "resolucao_insuficiente"),
    ("muito escura", dict(iluminacao=0.15, semente=4), "exposicao_inadequada"),
    ("estourada", dict(iluminacao=2.6, semente=4), "exposicao_inadequada"),
    ("tremida", dict(desfoque=7, semente=4), "desfocada"),
    ("tremida mais", dict(desfoque=9, semente=6), "desfocada"),
])
def test_imagem_ruim_vira_inconclusivo(cena, nome, kw, situacao):
    rodar, *_ = cena
    res, alin = rodar(**kw)
    assert alin.situacao == situacao, nome
    assert set(classes(res)) == {"indeterminado"} and all(c == 0.0 for _, _, c in res)


@pytest.mark.parametrize("nome,kw", [
    ("sem marcadores (todos ocultos)", dict(ocultar_marcadores=[0, 1, 2, 3], semente=4)),
    ("tremido extremo", dict(desfoque=15, semente=4)),
])
def test_sem_face_identificavel_recusa(cena, nome, kw):
    rodar, *_ = cena
    with pytest.raises(ImagemInadequada) as e:
        rodar(**kw)
    assert e.value.motivo == "marcadores_nao_encontrados"


def test_marcadores_de_outra_face_nao_alinham(cena):
    rodar, face, *_ = cena
    outra = dict(face, marcadores=[{"id": 40 + i, "x_mm": m["x_mm"], "y_mm": m["y_mm"]} for i, m in enumerate(face["marcadores"])])
    with pytest.raises(ImagemInadequada):
        rodar(faces={FID: outra}, semente=4)


def test_sem_referencia_e_inconclusivo(cena):
    rodar, face, *_ = cena
    res, alin = rodar(faces={FID: dict(face, referencia_sha256=None)}, semente=4)
    assert alin.situacao == "sem_referencia" and set(classes(res)) == {"indeterminado"}


def test_referencia_adulterada_nao_e_usada(cena):
    rodar, face, _, _, tmp = cena
    arq = tmp / f"{face['referencia_sha256']}.png"
    img = cv2.imread(str(arq)); cv2.rectangle(img, (100, 90), (400, 300), (205, 200, 195), -1)   # "apaga" produtos
    arq.write_bytes(cv2.imencode(".png", img)[1].tobytes())          # PNG válido, conteúdo diferente
    _, alin = rodar(semente=4)
    assert alin.situacao == "sem_referencia"


def test_referencia_exige_quatro_marcadores():
    mk, pos = layout_marcadores(), layout_posicoes()
    foto = fotografar(renderizar_face(["cheio"] * 8, pos, mk), ocultar_marcadores=[3], marcadores=mk)
    with pytest.raises(ValueError):
        gerar_referencia(cv2.imdecode(np.frombuffer(foto, np.uint8), 1), dict(FACE, marcadores=mk))


def test_fundo_perfurado_nao_gera_vazio_confiante_errado(tmp_path):
    """Limitação conhecida: em painel perfurado a textura do fundo engana a pista de textura.
    O método erra para o lado seguro: confiança abaixo do limiar (vira V), nunca D1 indevido."""
    face, mk, pos, posicoes = _montar(tmp_path, fundo="perfurado")
    f = tmp_path / "f.jpg"
    f.write_bytes(fotografar(renderizar_face(VERDADE, pos, mk, semente=5, fundo="perfurado"), semente=2))
    res = MetodoB1(tmp_path).classificar(f, {"faces": {FID: face}}, posicoes)
    for (pid, classe, conf), verdade in zip(res, VERDADE):
        if verdade != "vazio":
            assert not (classe == "vazio" and conf >= 0.8), pid       # nunca acusa vazio onde há produto
    assert all(conf < 0.8 for _, classe, conf in res if classe == "vazio")  # vazio real: inconclusivo aqui


def test_roteiro_de_validacao_real_funciona(tmp_path):
    """Executa o script de validação com uma pasta montada a partir de fotos sintéticas
    (só garante que o roteiro funciona; os números reais virão das fotos da equipe)."""
    import json, runpy, sys
    mk, pos = layout_marcadores(), layout_posicoes()
    (tmp_path / "fotos").mkdir()
    face = dict(FACE, marcadores=mk, posicoes=pos)
    (tmp_path / "face.json").write_text(json.dumps(face))
    (tmp_path / "referencia.jpg").write_bytes(fotografar(renderizar_face(["cheio"] * 8, pos, mk, semente=1), perspectiva=0.03, semente=1))
    (tmp_path / "fotos" / "a.jpg").write_bytes(fotografar(renderizar_face(VERDADE, pos, mk, semente=5), semente=2))
    linhas = ["arquivo;codigo;classe"] + [f"a.jpg;{p['codigo']};{ {'trocado': 'cheio'}.get(v, v)}" for p, v in zip(pos, VERDADE)]
    (tmp_path / "anotacoes.csv").write_text("\n".join(linhas))
    sys.argv = ["x", str(tmp_path)]
    runpy.run_path(str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "validar_fotos_reais.py"), run_name="__main__")
    rel = (tmp_path / "relatorio.txt").read_text()
    assert "1 fotos" in rel and "'vazio' confiante onde havia produto: 0" in rel
