"""Gera uma folha A4 com 4 marcadores ArUco em tamanho real, para imprimir sem ajuste de escala.
Uso: python scripts/gerar_marcadores.py --ids 0 1 2 3 --tamanho-mm 60 --saida marcadores.pdf
Confira com uma régua: o lado preto do marcador deve medir exatamente o tamanho pedido."""
import argparse
import cv2
import numpy as np
from PIL import Image

DPI = 300
MM = DPI / 25.4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs=4, required=True)
    ap.add_argument("--tamanho-mm", type=int, default=60)
    ap.add_argument("--saida", default="marcadores.pdf")
    a = ap.parse_args()
    w, h = int(210 * MM), int(297 * MM)
    folha = np.full((h, w), 255, np.uint8)
    lado = int(round(a.tamanho_mm * MM))
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for k, mid in enumerate(a.ids):
        x = int(25 * MM) + (k % 2) * int(95 * MM)
        y = int(30 * MM) + (k // 2) * int(120 * MM)
        folha[y:y + lado, x:x + lado] = cv2.aruco.generateImageMarker(d, mid, lado)
        cv2.putText(folha, f"id {mid}  ({a.tamanho_mm} mm)  canto: {['sup. esq.', 'sup. dir.', 'inf. dir.', 'inf. esq.'][k]}",
                    (x, y + lado + int(10 * MM)), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    Image.fromarray(folha).save(a.saida, resolution=DPI)
    print(f"{a.saida}: imprimir em A4, escala 100% (sem 'ajustar à página').")


if __name__ == "__main__":
    main()
