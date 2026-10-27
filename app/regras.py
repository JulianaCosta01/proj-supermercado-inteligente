"""Regra de alerta (versão r2). Função pura: recebe fatos, devolve o alerta ou None.

D1 vazio na gôndola, estoque disponível no sistema >= margem
D2 ocupado na gôndola, estoque no sistema <= 0
V  inconclusivo: baixa confiança, classe indeterminada, estoque ausente/velho,
   estoque positivo abaixo da margem, ou planograma com rejeição pendente
"""
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

VERSAO = "r2"


@dataclass(frozen=True)
class Fatos:
    classe: str                     # vazio | parcial | ocupado | indeterminado
    confianca: float
    limiar: float
    dia_captura: dt.date
    max_idade_dias: int
    snapshot_id: int | None = None
    snapshot_data: dt.date | None = None
    quantidade: Decimal | None = None
    consumo_estimado: Decimal | None = None   # vendas desde o snapshot; None se não houver dado de vendas
    margem: Decimal = Decimal(2)
    planograma_pendente: bool = False


def avaliar(f: Fatos):
    """Retorna (tipo, snapshot_id) ou None."""
    if f.classe == "parcial":
        return None
    if f.classe == "indeterminado" or f.confianca < f.limiar:
        return ("V", None)
    fresco = (f.snapshot_id is not None and f.snapshot_data is not None and f.quantidade is not None
              and f.dia_captura - dt.timedelta(days=f.max_idade_dias) <= f.snapshot_data <= f.dia_captura)
    if f.classe == "vazio":
        if not fresco:
            return ("V", None)
        disponivel = f.quantidade - (f.consumo_estimado or 0)
        if f.quantidade <= 0:
            return None                           # vazio e sem estoque: coerente
        if disponivel < f.margem:
            return ("V", None)                    # pode ter acabado depois do snapshot
        return ("V", None) if f.planograma_pendente else ("D1", f.snapshot_id)
    if f.classe == "ocupado":
        if not fresco or f.quantidade > 0:
            return None
        return ("V", None) if f.planograma_pendente else ("D2", f.snapshot_id)
    raise ValueError(f"classe desconhecida: {f.classe}")
