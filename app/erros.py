"""Tradução dos erros do banco para respostas HTTP. Detalhes só no log."""
import logging
import uuid
import psycopg
from fastapi import HTTPException

log = logging.getLogger("stp")

MAPA = {
    "STP03": (403, "operação não permitida"),
    "STP04": (404, "não encontrado"),
    "STP05": (409, "transição não permitida"),
    "STP06": (403, "loja sem autorização ativa"),
    "STP08": (422, "parâmetros inválidos"),
    "STP09": (409, "o registro mudou desde a última leitura"),
    "23505": (409, "registro duplicado"),
    "23514": (422, "valor inválido"),
}


def http_de_banco(e: psycopg.Error) -> HTTPException:
    codigo = getattr(e, "sqlstate", None)
    status, msg = MAPA.get(codigo, (503, "serviço indisponível"))
    ref = uuid.uuid4().hex[:12]
    log.warning("erro de banco ref=%s sqlstate=%s %s", ref, codigo, str(e).splitlines()[0][:200])
    return HTTPException(status, {"erro": msg, "ref": ref})
