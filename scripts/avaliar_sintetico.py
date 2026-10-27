"""Avaliação do método B1 em cenas SINTÉTICAS aleatórias (sementes fixas, reproduzível).

Mede o comportamento em condições controladas. NÃO é métrica de desempenho em loja.
Uso: python scripts/avaliar_sintetico.py [n_cenas]
"""
import collections
import pathlib
import sys
import tempfile

import cv2
import numpy as np

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ), str(RAIZ / "tests")]
from sintetico import FACE, layout_marcadores, layout_posicoes, renderizar_face, fotografar  # noqa: E402
from app.metodos import ImagemInadequada  # noqa: E402
from app.visao import MetodoB1, gerar_referencia  # noqa: E402

LIMIAR = 0.80
ESTADOS = ["cheio", "vazio", "parcial", "trocado"]


def main(n=60):
    tmp = pathlib.Path(tempfile.mkdtemp())
    resultados = {}
    for fundo in ("liso", "perfurado"):
        mk, pos = layout_marcadores(), layout_posicoes()
        face = dict(FACE, marcadores=mk)
        ref = fotografar(renderizar_face(["cheio"] * 8, pos, mk, semente=1, fundo=fundo), perspectiva=0.03, semente=1)
        png, sha = gerar_referencia(cv2.imdecode(np.frombuffer(ref, np.uint8), 1), face)
        (tmp / f"{sha}.png").write_bytes(png); face["referencia_sha256"] = sha
        posicoes = [dict(p, posicao_id=i, face_id=1) for i, p in enumerate(pos)]
        metodo = MetodoB1(tmp)
        conf_mat = collections.Counter(); situacoes = collections.Counter()
        for k in range(n):
            rng = np.random.default_rng(10_000 + k)
            verdade = list(rng.choice(ESTADOS, 8, p=[0.5, 0.2, 0.15, 0.15]))
            opcoes = [None, float(rng.uniform(0.6, 1.15)), (float(rng.uniform(0.45, 0.8)), 1.1)]
            ilum = opcoes[int(rng.integers(0, 3))]
            foto = fotografar(renderizar_face(verdade, pos, mk, semente=k, fundo=fundo),
                              perspectiva=float(rng.uniform(0, 0.2)), iluminacao=ilum,
                              desfoque=int(rng.choice([0, 0, 3, 5])), semente=k, escala=float(rng.uniform(0.9, 1.5)))
            f = tmp / "f.jpg"; f.write_bytes(foto)
            try:
                res = metodo.classificar(f, {"faces": {1: face}}, posicoes)
            except ImagemInadequada as e:
                situacoes["recusada:" + e.motivo] += 1; continue
            situacoes[metodo.ultimo_alinhamento.situacao] += 1
            for (pid, classe, conf), v in zip(res, verdade):
                definitivo = classe in ("vazio", "ocupado") and conf >= LIMIAR
                conf_mat[(v, classe + ("" if definitivo or classe not in ("vazio", "ocupado") else "(baixa)"))] += 1
        resultados[fundo] = (conf_mat, situacoes)

    print(f"AVALIAÇÃO SINTÉTICA do método {MetodoB1.nome} {MetodoB1.versao}: {n} cenas por fundo, sementes 10000+k")
    print("Não é validação em loja. Linhas = verdade gerada; colunas = saída do método (limiar de confiança 0,80).\n")
    for fundo, (cm, sit) in resultados.items():
        print(f"== fundo {fundo} ==   situação das fotos: {dict(sit)}")
        colunas = sorted({c for _, c in cm})
        print("verdade".ljust(10) + "".join(c.ljust(16) for c in colunas))
        for v in ESTADOS:
            print(v.ljust(10) + "".join(str(cm.get((v, c), 0)).ljust(16) for c in colunas))
        perigoso = sum(n for (v, c), n in cm.items() if c == "vazio" and v != "vazio")
        perdido = sum(n for (v, c), n in cm.items() if v == "vazio" and c != "vazio")
        print(f"  'vazio' confiante onde havia produto (geraria D1 indevido): {perigoso}")
        print(f"  vazio real sem 'vazio' confiante (vira V ou nada):          {perdido}\n")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
