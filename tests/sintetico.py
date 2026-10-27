"""Gerador de fotos SINTÉTICAS de uma face de gôndola com marcadores ArUco.

Serve para testar o pipeline com verdade conhecida. Não substitui validação com fotos
reais: texturas, reflexos e embalagens reais são mais variados do que este gerador.
"""
import cv2
import numpy as np

FACE = {"largura_mm": 1200, "altura_mm": 700, "marcador_tamanho_mm": 60, "aruco_dicionario": "DICT_4X4_50"}
MARGEM_BRANCA = 12


def layout_marcadores(base_id=0, face=FACE):
    s, w, h = face["marcador_tamanho_mm"], face["largura_mm"], face["altura_mm"]
    m = MARGEM_BRANCA
    return [{"id": base_id + 0, "x_mm": m, "y_mm": m}, {"id": base_id + 1, "x_mm": w - s - m, "y_mm": m},
            {"id": base_id + 2, "x_mm": w - s - m, "y_mm": h - s - m}, {"id": base_id + 3, "x_mm": m, "y_mm": h - s - m}]


def layout_posicoes(face=FACE, colunas=4, linhas=2):
    """Grade de posições dentro da área útil (entre os marcadores)."""
    x0, y0, x1, y1 = 100, 90, face["largura_mm"] - 100, face["altura_mm"] - 90
    lw, lh = (x1 - x0) // colunas, (y1 - y0) // linhas
    return [{"codigo": f"N{r + 1}-P{c + 1}", "x_mm": x0 + c * lw, "y_mm": y0 + r * lh, "largura_mm": lw - 10, "altura_mm": lh - 20}
            for r in range(linhas) for c in range(colunas)]


def _produto(img, x, y, w, h, rng):
    cor = tuple(int(v) for v in rng.integers(30, 230, 3))
    cv2.rectangle(img, (x, y), (x + w, y + h), cor, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (20, 20, 20), 2)
    for k in range(int(rng.integers(3, 7))):           # faixas e "texto" da embalagem
        yy = y + int(rng.integers(5, max(6, h - 10)))
        cv2.line(img, (x + 4, yy), (x + w - 4, yy), tuple(int(v) for v in rng.integers(0, 255, 3)), int(rng.integers(1, 4)))
    cv2.putText(img, "PROD", (x + 4, y + h // 2), cv2.FONT_HERSHEY_SIMPLEX, max(0.3, w / 120), (255, 255, 255), 1)
    cv2.circle(img, (x + w // 2, y + h // 3), max(3, w // 6), tuple(int(v) for v in rng.integers(0, 255, 3)), 2)


def renderizar_face(ocupacao, posicoes, marcadores, face=FACE, semente=0, fundo="liso", etiquetas=True):
    """ocupacao: lista com 'cheio' | 'vazio' | 'parcial' | 'trocado' por posição. Retorna BGR na escala 1 px = 1 mm."""
    rng = np.random.default_rng(semente)
    w, h = face["largura_mm"], face["altura_mm"]
    img = np.full((h, w, 3), (205, 200, 195), np.uint8)
    img = cv2.add(img, rng.integers(0, 6, img.shape, dtype=np.uint8))       # textura leve do fundo
    if fundo == "perfurado":                                                 # painel perfurado comum em gôndolas
        for yy in range(20, h, 25):
            for xx in range(20, w, 25):
                cv2.circle(img, (xx, yy), 3, (90, 90, 90), -1)
    for p in posicoes:                                                       # prateleiras
        base = p["y_mm"] + p["altura_mm"] + 5
        cv2.rectangle(img, (60, base), (w - 60, base + 10), (90, 90, 95), -1)
        if etiquetas:                                                        # etiqueta de preço sempre visível
            cv2.rectangle(img, (p["x_mm"] + 10, base + 1), (p["x_mm"] + 60, base + 9), (240, 240, 240), -1)
    for idx, (p, estado) in enumerate(zip(posicoes, ocupacao)):
        x, y, pw, ph = p["x_mm"], p["y_mm"], p["largura_mm"], p["altura_mm"]
        if estado == "vazio":
            continue
        # aparência do produto fixa por posição (o planograma define o produto);
        # 'trocado' simula outro produto colocado no lugar
        rng_p = np.random.default_rng(1000 + idx + (500 if estado == "trocado" else 0))
        frentes = 2 if estado == "parcial" else 4
        largura_util = pw // 2 if estado == "parcial" else pw
        fw = largura_util // frentes
        for k in range(frentes):
            alt = int(ph * rng_p.uniform(0.75, 0.95))
            _produto(img, x + k * fw + 3, y + ph - alt, fw - 6, alt, rng_p)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    s, m = face["marcador_tamanho_mm"], MARGEM_BRANCA
    for mk in marcadores:
        x, y = mk["x_mm"], mk["y_mm"]
        cv2.rectangle(img, (x - m, y - m), (x + s + m, y + s + m), (255, 255, 255), -1)
        img[y:y + s, x:x + s] = cv2.cvtColor(cv2.aruco.generateImageMarker(d, mk["id"], s), cv2.COLOR_GRAY2BGR)
    return img


def fotografar(face_img, perspectiva=0.10, escala=1.3, iluminacao=None, desfoque=0, ruido=4.0,
               ocultar_marcadores=(), marcadores=None, semente=0, tamanho=(2000, 1500), qualidade_jpeg=88,
               face=FACE, deslocamento=(0, 0)):
    """Projeta a face numa 'foto' com perspectiva, luz, desfoque, ruído e compressão JPEG."""
    rng = np.random.default_rng(semente)
    img = face_img.copy()
    s, m = face["marcador_tamanho_mm"], MARGEM_BRANCA
    for mid in ocultar_marcadores:                       # oclusão: cobre o marcador (ex.: mão, produto)
        mk = next(x for x in marcadores if x["id"] == mid)
        cv2.rectangle(img, (mk["x_mm"] - m, mk["y_mm"] - m), (mk["x_mm"] + s + m, mk["y_mm"] + s + m), (60, 40, 30), -1)
    W, Hh = tamanho
    fw, fh = face["largura_mm"] * escala, face["altura_mm"] * escala
    cx, cy = W / 2 + deslocamento[0], Hh / 2 + deslocamento[1]
    base = np.array([[cx - fw / 2, cy - fh / 2], [cx + fw / 2, cy - fh / 2], [cx + fw / 2, cy + fh / 2], [cx - fw / 2, cy + fh / 2]])
    j = rng.uniform(-1, 1, (4, 2)) * perspectiva * np.array([fw, fh])
    dst = (base + j).astype(np.float32)
    src = np.float32([[0, 0], [face["largura_mm"], 0], [face["largura_mm"], face["altura_mm"]], [0, face["altura_mm"]]])
    Hm = cv2.getPerspectiveTransform(src, dst)
    fundo = rng.integers(40, 120, (Hh, W, 3), dtype=np.uint8)
    fundo = cv2.GaussianBlur(fundo, (31, 31), 0)
    foto = cv2.warpPerspective(img, Hm, (W, Hh), dst=fundo, borderMode=cv2.BORDER_TRANSPARENT)
    foto = foto.astype(np.float32)
    if iluminacao is not None:                          # ganho global ou gradiente (sombra lateral)
        if isinstance(iluminacao, tuple):
            g = np.linspace(iluminacao[0], iluminacao[1], W, dtype=np.float32)[None, :, None]
            foto *= g
        else:
            foto *= float(iluminacao)
    foto += rng.normal(0, ruido, foto.shape).astype(np.float32)
    foto = np.clip(foto, 0, 255).astype(np.uint8)
    if desfoque:
        k = np.zeros((desfoque, desfoque), np.float32); k[desfoque // 2, :] = 1.0 / desfoque     # tremido horizontal
        foto = cv2.filter2D(foto, -1, k)
    ok, jpg = cv2.imencode(".jpg", foto, [cv2.IMWRITE_JPEG_QUALITY, qualidade_jpeg])
    return jpg.tobytes()
