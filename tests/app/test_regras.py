"""Regra de alerta r2: tabela de casos. Cada linha é um cenário do item A3 da revisão 0.5.1."""
import datetime as dt
from decimal import Decimal as D
import pytest
from app.regras import Fatos, avaliar

HOJE = dt.date(2026, 10, 20)


def f(classe="vazio", conf=0.95, snap=True, data=HOJE, qtd=D(10), consumo=None, pendente=False):
    return Fatos(classe=classe, confianca=conf, limiar=0.8, dia_captura=HOJE, max_idade_dias=1,
                 snapshot_id=7 if snap else None, snapshot_data=data if snap else None,
                 quantidade=qtd if snap else None, consumo_estimado=consumo, planograma_pendente=pendente)


@pytest.mark.parametrize("fatos,esperado", [
    (f(), ("D1", 7)),
    (f(data=HOJE - dt.timedelta(days=1)), ("D1", 7)),            # snapshot de ontem: dentro da janela
    (f(data=HOJE - dt.timedelta(days=2)), ("V", None)),          # velho
    (f(data=HOJE + dt.timedelta(days=1)), ("V", None)),          # do futuro
    (f(snap=False), ("V", None)),                                # sem estoque registrado
    (f(conf=0.5), ("V", None)),                                  # baixa confiança
    (f(classe="indeterminado"), ("V", None)),
    (f(qtd=D(1)), ("V", None)),                                  # abaixo da margem: pode ter acabado
    (f(qtd=D(10), consumo=D(9)), ("V", None)),                   # vendas desde o snapshot consumiram
    (f(qtd=D(0)), None),                                         # vazio e sem estoque: coerente
    (f(qtd=D(-3)), None),
    (f(pendente=True), ("V", None)),                             # planograma em revisão
    (f(classe="ocupado", qtd=D(0)), ("D2", 7)),
    (f(classe="ocupado", qtd=D(-2)), ("D2", 7)),
    (f(classe="ocupado", qtd=D(5)), None),
    (f(classe="ocupado", qtd=D(0), data=HOJE - dt.timedelta(days=3)), None),   # velho: não acusa
    (f(classe="ocupado", qtd=D(0), pendente=True), ("V", None)),
    (f(classe="parcial"), None),
])
def test_regra(fatos, esperado):
    assert avaliar(fatos) == esperado


def test_classe_desconhecida():
    with pytest.raises(ValueError):
        avaliar(f(classe="xyz"))
