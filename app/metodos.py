"""Métodos de ocupação. Interface: classificar(imagem, captura, posicoes) -> [(posicao_id, classe, confianca)].

O método de visão real (alinhamento ArUco + linha de base B1) é a próxima entrega e ainda
NÃO existe. O worker recebe o método por injeção; os testes usam um método de teste.
"""
from typing import Protocol


class Metodo(Protocol):
    nome: str
    versao: str

    def classificar(self, imagem_path, captura: dict, posicoes: list[dict]) -> list[tuple[int, str, float]]: ...
