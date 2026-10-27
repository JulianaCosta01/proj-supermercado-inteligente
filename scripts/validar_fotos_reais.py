"""Validação do método B1 com FOTOS REAIS (gôndola real ou montada pela equipe).

Estrutura esperada da pasta:
  face.json          geometria da face (mesmo formato de app/cadastro.py), posições com "codigo"
  referencia.jpg     foto da face abastecida, com os 4 marcadores visíveis
  fotos/*.jpg        fotos a avaliar
  anotacoes.csv      arquivo;codigo;classe  (classe: cheio | vazio | parcial), anotado por uma pessoa
Saída: matriz de confusão e situação das fotos, impressas e gravadas em <pasta>/relatorio.txt.

Nenhuma métrica de loja deve ser divulgada sem este relatório e sem dizer quantas fotos o sustentam.
"""
import collections
import csv
import json
import pathlib
import sys
import tempfile

import cv2
import numpy as np

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from app.metodos import ImagemInadequada  # noqa: E402
from app.visao import MetodoB1, gerar_referencia  # noqa: E402

LIMIAR = 0.80
EQUIV = {"cheio": "ocupado", "vazio": "vazio", "parcial": "parcial"}


def main(pasta):
    pasta = pathlib.Path(pasta)
    face = json.loads((pasta / "face.json").read_text(encoding="utf-8"))
    tmp = pathlib.Path(tempfile.mkdtemp())
    ref = cv2.imread(str(pasta / "referencia.jpg"))
    png, sha = gerar_referencia(ref, face)
    (tmp / f"{sha}.png").write_bytes(png); face["referencia_sha256"] = sha
    posicoes = [dict(p, posicao_id=i, face_id=1) for i, p in enumerate(face["posicoes"])]
    cod = {i: p["codigo"] for i, p in enumerate(face["posicoes"])}
    verdade = collections.defaultdict(dict)
    with open(pasta / "anotacoes.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            verdade[r["arquivo"]][r["codigo"]] = r["classe"]
    metodo, cm, sit, n_fotos = MetodoB1(tmp), collections.Counter(), collections.Counter(), 0
    for foto in sorted((pasta / "fotos").glob("*.jpg")):
        n_fotos += 1
        try:
            res = metodo.classificar(foto, {"faces": {1: face}}, posicoes)
        except ImagemInadequada as e:
            sit["recusada:" + e.motivo] += 1; continue
        sit[metodo.ultimo_alinhamento.situacao] += 1
        for pid, classe, conf in res:
            v = verdade.get(foto.name, {}).get(cod[pid])
            if v is None:
                continue
            definitivo = classe in ("vazio", "ocupado") and conf >= LIMIAR
            cm[(v, classe if definitivo or classe not in ("vazio", "ocupado") else classe + "(baixa)")] += 1
    linhas = [f"VALIDAÇÃO COM FOTOS REAIS: {n_fotos} fotos em {pasta.name}", f"situação: {dict(sit)}"]
    colunas = sorted({c for _, c in cm})
    linhas.append("verdade".ljust(10) + "".join(c.ljust(16) for c in colunas))
    for v in EQUIV:
        linhas.append(v.ljust(10) + "".join(str(cm.get((v, c), 0)).ljust(16) for c in colunas))
    linhas.append(f"'vazio' confiante onde havia produto: {sum(n for (v, c), n in cm.items() if c == 'vazio' and v != 'vazio')}")
    texto = "\n".join(linhas)
    print(texto)
    (pasta / "relatorio.txt").write_text(texto + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
