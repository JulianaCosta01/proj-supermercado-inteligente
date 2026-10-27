"""Linha de base B1: alinhamento por marcadores ArUco + comparação com a foto de referência.

Entrada (contrato do worker): imagem já re-codificada pelo worker (JPEG confiável),
a captura (com captura["faces"]: geometria das faces da loja) e as posições vigentes.

Saída: lista (posicao_id, classe, confianca) só para posições da face identificada, e
self.ultimo_alinhamento com a face, a situação e as métricas (gravados com a conclusão).

Política de segurança do resultado:
  * nenhum marcador conhecido           -> ImagemInadequada (captura recusada, sem observações)
  * face identificada, mas imagem ruim  -> todas as posições 'indeterminado' (vira alerta V)
  * posição fora do quadro / saturada   -> 'indeterminado' só para ela
  * confiança é limitada pela qualidade do alinhamento: imagem ruim nunca gera D1/D2
"""
import hashlib
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.metodos import ImagemInadequada

PX_POR_MM = 1.0
ERRO_MAX_MM = 4.0
RESOLUCAO_MIN_PX_POR_MM = 0.6
BRILHO_MIN, BRILHO_MAX = 35.0, 220.0
NITIDEZ_RELATIVA_MIN = 0.48
NITIDEZ_ABSOLUTA_MIN = 0.002
LIMIAR_VAZIO, LIMIAR_OCUPADO = 0.20, 0.55
TEXTURA_REF_MIN = 0.02
MARGEM_RECORTE = 0.08
SATURACAO_MAX = 0.25

DICIONARIOS = {"DICT_4X4_50": cv2.aruco.DICT_4X4_50, "DICT_5X5_100": cv2.aruco.DICT_5X5_100}


@dataclass
class Alinhamento:
    face_id: int
    situacao: str
    marcadores: int
    erro_mm: float | None = None
    metricas: dict = field(default_factory=dict)


def detectar_marcadores(img_bgr, dicionario="DICT_4X4_50"):
    """Retorna {id: cantos 4x2 (TL, TR, BR, BL) em pixels}."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICIONARIOS[dicionario]), params)
    cantos, ids, _ = det.detectMarkers(gray)
    if ids is None:
        return {}
    res = {}
    for c, i in zip(cantos, ids.flatten()):
        i = int(i)
        if i in res:                       # id repetido na mesma foto: ambíguo, descarta os dois
            res[i] = None
        else:
            res[i] = c.reshape(4, 2).astype(np.float64)
    return {k: v for k, v in res.items() if v is not None}


def cantos_face_mm(x, y, s):
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], dtype=np.float64)


def textura(cinza):
    """Densidade de bordas após equalização local (robusta a iluminação)."""
    eq = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(cinza)
    v = float(np.median(eq))
    bordas = cv2.Canny(eq, max(0, int(0.66 * v)), min(255, int(1.33 * v) + 1))
    return float(np.count_nonzero(bordas)) / bordas.size


def fracao_faixas_iguais(atual_bgr, ref_bgr, faixas=8, limiar=0.45):
    """Fração de faixas verticais cuja distribuição de cor (H-S) se parece com a referência.
    Usa só matiz e saturação: pouco sensível a mudanças de brilho."""
    if atual_bgr.shape[1] < faixas * 4:
        return None
    a, r = cv2.cvtColor(atual_bgr, cv2.COLOR_BGR2HSV), cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2HSV)
    passo, iguais = atual_bgr.shape[1] // faixas, 0
    for k in range(faixas):
        sa, sr = a[:, k * passo:(k + 1) * passo], r[:, k * passo:(k + 1) * passo]
        ha = cv2.calcHist([sa], [0, 1], None, [18, 8], [0, 180, 0, 256]); cv2.normalize(ha, ha, 1, 0, cv2.NORM_L1)
        hr = cv2.calcHist([sr], [0, 1], None, [18, 8], [0, 180, 0, 256]); cv2.normalize(hr, hr, 1, 0, cv2.NORM_L1)
        if cv2.compareHist(ha, hr, cv2.HISTCMP_BHATTACHARYYA) < limiar:
            iguais += 1
    return iguais / faixas


def nitidez(cinza):
    """Variância do Laplaciano após suavização leve (o ruído do sensor não conta), dividida pela
    variância da imagem: assim uma foto mais escura não parece mais desfocada."""
    g = cv2.GaussianBlur(cinza, (5, 5), 0).astype(np.float64)
    return float(cv2.Laplacian(g, cv2.CV_64F).var() / max(g.var(), 1e-6))


def retificar(img_bgr, face, detectados):
    """Homografia imagem -> plano da face (mm). Retorna (retificada, erro_mm, H, n) ou levanta ValueError."""
    ids = [m for m in face["marcadores"] if m["id"] in detectados]
    src = np.vstack([detectados[m["id"]] for m in ids])
    dst = np.vstack([cantos_face_mm(m["x_mm"], m["y_mm"], face["marcador_tamanho_mm"]) for m in ids]) * PX_POR_MM
    H, _ = cv2.findHomography(src, dst, 0)            # mínimos quadrados: determinístico
    if H is None or not np.isfinite(H).all():
        raise ValueError("homografia degenerada")
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    erro_mm = float(np.mean(np.linalg.norm(proj - dst, axis=1))) / PX_POR_MM
    w, h = int(face["largura_mm"] * PX_POR_MM), int(face["altura_mm"] * PX_POR_MM)
    ret = cv2.warpPerspective(img_bgr, H, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return ret, erro_mm, H, len(ids)


def resolucao_px_por_mm(H, face):
    """Pixels da foto por mm da face (pior eixo), estimado pelos cantos da face."""
    w, h = face["largura_mm"] * PX_POR_MM, face["altura_mm"] * PX_POR_MM
    cantos = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64).reshape(-1, 1, 2)
    img = cv2.perspectiveTransform(cantos, np.linalg.inv(H)).reshape(-1, 2)
    larg = min(np.linalg.norm(img[1] - img[0]), np.linalg.norm(img[2] - img[3])) / face["largura_mm"]
    alt = min(np.linalg.norm(img[3] - img[0]), np.linalg.norm(img[2] - img[1])) / face["altura_mm"]
    return float(min(larg, alt)), img


def recorte(img, p, margem=MARGEM_RECORTE):
    x, y, w, h = (int(round(v * PX_POR_MM)) for v in (p["x_mm"], p["y_mm"], p["largura_mm"], p["altura_mm"]))
    mx, my = int(w * margem), int(h * margem)
    return img[y + my:y + h - my, x + mx:x + w - mx]


def dentro_do_quadro(H, p, forma):
    x, y, w, h = p["x_mm"], p["y_mm"], p["largura_mm"], p["altura_mm"]
    q = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], dtype=np.float64).reshape(-1, 1, 2) * PX_POR_MM
    img = cv2.perspectiveTransform(q, np.linalg.inv(H)).reshape(-1, 2)
    alt, larg = forma[:2]
    return bool((img[:, 0] >= 0).all() and (img[:, 0] < larg).all() and (img[:, 1] >= 0).all() and (img[:, 1] < alt).all())


def combinar(r, f):
    """Combina as duas pistas. Só dá confiança alta quando elas concordam.
    r: textura atual / textura da referência; f: fração de faixas com a mesma cor da referência."""
    if f is None:
        return "indeterminado", 0.0
    vazio_t, ocupado_t = r <= LIMIAR_VAZIO, r >= LIMIAR_OCUPADO
    vazio_c, ocupado_c = f <= 0.25, f >= 0.75
    if vazio_c and (vazio_t or r < LIMIAR_OCUPADO):
        forca = (0.25 - f) / 0.25
        conf = 0.6 + 0.4 * forca if vazio_t else 0.55 + 0.2 * forca       # sem apoio da textura: abaixo do limiar
        return "vazio", conf
    if ocupado_c and ocupado_t:
        return "ocupado", 0.6 + 0.4 * min(1.0, (f - 0.75) / 0.25)
    if ocupado_t and not vazio_c:
        return "ocupado", 0.5 + 0.2 * f                                    # textura sim, cor diferente: produto trocado?
    return "parcial", 0.5


class MetodoB1:
    nome = "B1-referencia"
    versao = "1.0.0"

    def __init__(self, dir_referencias):
        self.dir_referencias = dir_referencias
        self.ultimo_alinhamento: Alinhamento | None = None

    def _referencia(self, face):
        sha = face.get("referencia_sha256")
        if not sha:
            return None
        caminho = self.dir_referencias / f"{sha}.png"
        if not caminho.exists():
            return None
        dados = caminho.read_bytes()
        if hashlib.sha256(dados).hexdigest() != sha:          # referência adulterada: não usar
            return None
        return cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)

    def classificar(self, imagem_path, captura, posicoes):
        self.ultimo_alinhamento = None
        img = cv2.imread(str(imagem_path), cv2.IMREAD_COLOR)
        if img is None:
            raise ImagemInadequada("imagem_ilegivel")
        faces = captura.get("faces", {})
        if not faces:
            raise ImagemInadequada("loja_sem_faces_cadastradas")

        detectados = {}
        for dic in {f["aruco_dicionario"] for f in faces.values()}:
            detectados.update(detectar_marcadores(img, dic))
        contagem = {fid: sum(1 for m in f["marcadores"] if m["id"] in detectados) for fid, f in faces.items()}
        melhor = max(contagem.values())
        if melhor == 0:
            raise ImagemInadequada("marcadores_nao_encontrados")
        candidatas = [fid for fid, n in contagem.items() if n == melhor]
        if len(candidatas) > 1:
            raise ImagemInadequada("faces_ambiguas")
        fid = candidatas[0]
        face = faces[fid]
        alvo = [p for p in posicoes if p.get("face_id") == fid]

        def tudo_indeterminado(situacao, **metricas):
            self.ultimo_alinhamento = Alinhamento(fid, situacao, melhor, metricas.pop("erro_mm", None), metricas)
            return [(p["posicao_id"], "indeterminado", 0.0) for p in alvo]

        if melhor < 3:
            return tudo_indeterminado("marcadores_insuficientes")
        try:
            ret, erro_mm, H, n = retificar(img, face, detectados)
        except ValueError:
            return tudo_indeterminado("alinhamento_impreciso")
        res, _ = resolucao_px_por_mm(H, face)
        m = {"erro_mm": round(erro_mm, 2), "resolucao_px_mm": round(res, 3)}
        if erro_mm > ERRO_MAX_MM:
            return tudo_indeterminado("alinhamento_impreciso", **m)
        if res < RESOLUCAO_MIN_PX_POR_MM:
            return tudo_indeterminado("resolucao_insuficiente", **m)

        cinza = cv2.cvtColor(ret, cv2.COLOR_BGR2GRAY)
        brilho, nit = float(cinza.mean()), nitidez(cinza)
        m.update(brilho=round(brilho, 1), nitidez=round(nit, 1))
        if not (BRILHO_MIN <= brilho <= BRILHO_MAX):
            return tudo_indeterminado("exposicao_inadequada", **m)
        ref = self._referencia(face)
        if ref is None or ref.shape != ret.shape:
            return tudo_indeterminado("sem_referencia", **m)
        ref_cinza = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
        nit_ref = nitidez(ref_cinza)
        m["nitidez_ref"] = round(nit_ref, 1)
        if nit < max(NITIDEZ_ABSOLUTA_MIN, NITIDEZ_RELATIVA_MIN * nit_ref):
            return tudo_indeterminado("desfocada", **m)

        # qualidade do alinhamento limita a confiança: 1.0 com erro 0, 0.85 no limite aceito
        fator = 1.0 - 0.15 * (erro_mm / ERRO_MAX_MM)
        resultado, detalhes = [], {}
        for p in alvo:
            if not dentro_do_quadro(H, p, img.shape):
                resultado.append((p["posicao_id"], "indeterminado", 0.0)); detalhes[p["posicao_id"]] = "fora_do_quadro"
                continue
            atual, base = recorte(cinza, p), recorte(ref_cinza, p)
            saturado = float(np.mean((atual > 250) | (atual < 5)))
            t_ref = textura(base)
            if saturado > SATURACAO_MAX or t_ref < TEXTURA_REF_MIN:
                resultado.append((p["posicao_id"], "indeterminado", 0.0))
                detalhes[p["posicao_id"]] = "saturada" if saturado > SATURACAO_MAX else "referencia_sem_textura"
                continue
            r = textura(atual) / t_ref
            f = fracao_faixas_iguais(recorte(ret, p), recorte(ref, p))
            classe, conf = combinar(r, f)
            resultado.append((p["posicao_id"], classe, round(min(1.0, conf) * fator, 3)))
            detalhes[p["posicao_id"]] = {"textura": round(r, 3), "cor": f}
        m["razao_textura"] = {str(k): v for k, v in detalhes.items()}
        self.ultimo_alinhamento = Alinhamento(fid, "alinhada", n, round(erro_mm, 2), m)
        return resultado


def gerar_referencia(img_bgr, face):
    """Retifica uma foto da face abastecida e devolve (png_bytes, sha256). Falha se não alinhar bem."""
    det = detectar_marcadores(img_bgr, face["aruco_dicionario"])
    if sum(1 for m in face["marcadores"] if m["id"] in det) < 4:
        raise ValueError("a foto de referência precisa dos 4 marcadores visíveis")
    ret, erro_mm, H, _ = retificar(img_bgr, face, det)
    if erro_mm > ERRO_MAX_MM / 2:
        raise ValueError(f"alinhamento impreciso na referência ({erro_mm:.1f} mm)")
    ok, png = cv2.imencode(".png", ret)
    dados = png.tobytes()
    return dados, hashlib.sha256(dados).hexdigest()
