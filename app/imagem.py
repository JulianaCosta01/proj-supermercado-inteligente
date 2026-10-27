"""Decodificação isolada de imagem não confiável.

Executado SEMPRE em subprocesso (python -m app.imagem ENTRADA SAIDA) com limites de
memória e CPU aplicados pelo processo pai. Saída: JPEG re-codificado sem metadados.
Códigos de saída: 0 ok | 2 imagem inválida (recusa definitiva) | outro: falha (nova tentativa).
"""
import hashlib
import json
import sys
import warnings

MAX_PIXELS = 40_000_000
FORMATOS = {"JPEG", "PNG"}


def recodificar(entrada: str, saida: str) -> dict:
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(entrada) as im:
        if im.format not in FORMATOS:
            raise ValueError(f"formato {im.format} não aceito")
        w, h = im.size
        if w * h > MAX_PIXELS or w < 64 or h < 64:
            raise ValueError(f"dimensões fora do limite: {w}x{h}")
        im.load()
        im = ImageOps.exif_transpose(im)        # aplica a orientação antes de descartar o EXIF
        im = im.convert("RGB")
        im.save(saida, "JPEG", quality=90, optimize=True)   # sem exif=: nenhum metadado é copiado
    with open(saida, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return {"largura": im.size[0], "altura": im.size[1], "sha256": sha}


if __name__ == "__main__":
    try:
        print(json.dumps(recodificar(sys.argv[1], sys.argv[2])))
    except MemoryError:
        sys.exit(3)
    except Exception as e:          # qualquer erro de decodificação = imagem inválida
        print(json.dumps({"erro": f"{type(e).__name__}: {str(e)[:150]}"}))
        sys.exit(2)
