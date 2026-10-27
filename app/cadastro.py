"""Cadastro de faces de gôndola e de fotos de referência (tarefa administrativa).

Executado com a credencial de migração (papel stp_migrator), nunca pela API ou pelo worker:
  python -m app.cadastro face   --loja 1 --arquivo face.json
  python -m app.cadastro referencia --loja 1 --face G01-A --foto abastecida.jpg

face.json:
  {"codigo": "G01-A", "largura_mm": 1200, "altura_mm": 700, "marcador_tamanho_mm": 60,
   "marcadores": [{"id": 0, "x_mm": 12, "y_mm": 12}, ...4 marcadores...],
   "posicoes": [{"codigo": "N1-P1", "produto_id": 10, "x_mm": 100, "y_mm": 90, "largura_mm": 240, "altura_mm": 240}, ...]}
"""
import argparse
import json
import os
import pathlib

import cv2
import numpy as np
import psycopg

from app.config import Config
from app.visao import gerar_referencia


def cadastrar_face(conn, loja_id: int, d: dict) -> int:
    """Cria face, marcadores e posições (com planograma vigente) numa transação."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE stp_migrator")
        fid = conn.execute("""INSERT INTO op.face_gondola (loja_id, codigo, largura_mm, altura_mm, marcador_tamanho_mm, aruco_dicionario)
                              VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                           (loja_id, d["codigo"], d["largura_mm"], d["altura_mm"], d["marcador_tamanho_mm"],
                            d.get("aruco_dicionario", "DICT_4X4_50"))).fetchone()[0]
        if len(d["marcadores"]) != 4:
            raise ValueError("a face precisa de exatamente 4 marcadores")
        for m in d["marcadores"]:
            conn.execute("INSERT INTO op.face_marcador (face_id, loja_id, marcador_id, x_mm, y_mm) VALUES (%s, %s, %s, %s, %s)",
                         (fid, loja_id, m["id"], m["x_mm"], m["y_mm"]))
        for p in d["posicoes"]:
            pid = conn.execute("""INSERT INTO op.posicao (loja_id, codigo, face_id, x_mm, y_mm, largura_mm, altura_mm)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                               (loja_id, f'{d["codigo"]}/{p["codigo"]}', fid, p["x_mm"], p["y_mm"], p["largura_mm"], p["altura_mm"])).fetchone()[0]
            conn.execute("INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange(%s, NULL))",
                         (pid, p["produto_id"], p.get("vigente_de", "2026-01-01")))
    return fid


def face_do_banco(conn, face_id: int) -> dict:
    larg, alt, dic, tam = conn.execute("SELECT largura_mm, altura_mm, aruco_dicionario, marcador_tamanho_mm FROM op.face_gondola WHERE id = %s",
                                       (face_id,)).fetchone()
    marc = [{"id": i, "x_mm": x, "y_mm": y} for i, x, y in
            conn.execute("SELECT marcador_id, x_mm, y_mm FROM op.face_marcador WHERE face_id = %s", (face_id,)).fetchall()]
    return {"largura_mm": larg, "altura_mm": alt, "aruco_dicionario": dic, "marcador_tamanho_mm": tam, "marcadores": marc}


def registrar_referencia(conn, cfg: Config, face_id: int, foto_bytes: bytes) -> str:
    """Retifica a foto da face abastecida, grava em referencias/<sha>.png e aponta a face para ela."""
    img = cv2.imdecode(np.frombuffer(foto_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("foto ilegível")
    png, sha = gerar_referencia(img, face_do_banco(conn, face_id))
    destino = cfg.referencias / f"{sha}.png"
    tmp = cfg.tmp / f"{sha}.png.part"
    tmp.write_bytes(png)
    os.replace(tmp, destino)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE stp_migrator")
        conn.execute("UPDATE op.face_gondola SET referencia_sha256 = %s WHERE id = %s", (sha, face_id))
    return sha


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("face"); a.add_argument("--loja", type=int, required=True); a.add_argument("--arquivo", required=True)
    b = sub.add_parser("referencia"); b.add_argument("--loja", type=int, required=True); b.add_argument("--face", required=True); b.add_argument("--foto", required=True)
    args = ap.parse_args()
    cfg = Config(); cfg.preparar_diretorios()
    with psycopg.connect(os.environ["STP_DSN_ADMIN"]) as conn:
        if args.cmd == "face":
            print(cadastrar_face(conn, args.loja, json.loads(pathlib.Path(args.arquivo).read_text())))
        else:
            fid = conn.execute("SELECT id FROM op.face_gondola WHERE loja_id = %s AND codigo = %s", (args.loja, args.face)).fetchone()[0]
            print(registrar_referencia(conn, cfg, fid, pathlib.Path(args.foto).read_bytes()))


if __name__ == "__main__":
    main()
