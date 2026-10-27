"""Contrato dos métodos de ocupação usados pelo worker.

classificar(imagem_path, captura, posicoes) -> [(posicao_id, classe, confianca)]
  captura: id, loja_id, capturado_em, dia, max_idade, faces {face_id: geometria e marcadores}
  posicoes: posicao_id, codigo, produto_id, inicio_vigencia, face_id, x_mm, y_mm, largura_mm, altura_mm
  classe: vazio | parcial | ocupado | indeterminado; confianca em [0, 1]
Depois de classificar, o método expõe ultimo_alinhamento (face e qualidade) ou None.
Se a foto não puder ser associada a nenhuma face, levanta ImagemInadequada: a captura é recusada.
"""
from typing import Protocol


class ImagemInadequada(Exception):
    """A imagem não permite identificar a face; a captura é recusada com este motivo."""
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class Metodo(Protocol):
    nome: str
    versao: str

    def classificar(self, imagem_path, captura: dict, posicoes: list[dict]) -> list[tuple[int, str, float]]: ...
