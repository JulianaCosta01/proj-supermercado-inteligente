"""Reconciliação entre arquivos e banco. Executada pelo worker (mesmo acesso a arquivos).

Regras de segurança (testadas em tests/app/test_reconciliacao.py):
  1. lista os arquivos ANTES de consultar o banco; arquivo novo nunca é candidato (idade mínima)
  2. aborta se o banco falhar, ou se o banco não tiver capturas enquanto existem arquivos
  3. trava: aborta se os candidatos passarem de 5% dos arquivos (e de 10) ou de 200
  4. nada é apagado direto: candidatos vão para <área>/.lixeira/AAAAMMDD/ e só saem após 7 dias
  5. arquivo de quarentena de captura finalizada é apagado (o dado bruto não deve ficar)
  6. captura processada sem arquivo é registrada em op.imagem_ausente (não apaga nada no banco)
"""
import datetime as dt
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field

import psycopg

log = logging.getLogger("stp.reconciliacao")
IDADE_ORFAO_S = 24 * 3600
IDADE_TMP_S = 3600
DIAS_LIXEIRA = 7


@dataclass
class Resultado:
    abortada: str | None = None
    movidos_lixeira: list = field(default_factory=list)
    quarentena_apagada: list = field(default_factory=list)
    imagens_ausentes: list = field(default_factory=list)
    tmp_apagados: int = 0
    lixeira_esvaziada: int = 0


def _arquivos(cfg):
    agora = time.time()
    q = {p.name: agora - p.stat().st_mtime for p in cfg.quarentena.iterdir() if p.is_file()}
    im = {f"{p.parent.name}/{p.name}": agora - p.stat().st_mtime
          for p in cfg.imagens.glob("??/*.jpg") if p.is_file()}
    return q, im


def _para_lixeira(cfg, area, origem, rel):
    destino = cfg.lixeira_de(area) / dt.date.today().strftime("%Y%m%d") / rel.replace("/", "_")
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.replace(origem, destino)


def reconciliar(cfg, dsn_worker: str, agora_fn=time.time) -> Resultado:
    res = Resultado()
    # Restos de uploads ou recodificações interrompidos.
    for tmpdir in (cfg.tmp_quarentena, cfg.tmp_imagens):
        if not tmpdir.is_dir():
            continue
        for p in tmpdir.iterdir():
            if p.is_file() and agora_fn() - p.stat().st_mtime > IDADE_TMP_S:
                p.unlink(missing_ok=True); res.tmp_apagados += 1

    q_arqs, im_arqs = _arquivos(cfg)                       # (1) arquivos primeiro
    total = len(q_arqs) + len(im_arqs)
    try:
        with psycopg.connect(dsn_worker, autocommit=True, connect_timeout=5) as c:
            c.execute("SET default_transaction_read_only = on")
            n_capturas = c.execute("SELECT count(*) FROM op.captura").fetchone()[0]
            capturas = c.execute("SELECT quarentena_chave::text, estado, imagem_chave, id FROM op.captura").fetchall()
    except psycopg.Error as e:
        res.abortada = f"banco indisponível: {type(e).__name__}"      # (2)
        log.error("reconciliação abortada: %s", res.abortada)
        return res
    if n_capturas == 0 and total > 0:
        res.abortada = "banco sem capturas e há arquivos: possível restauração incompleta"
        log.error("reconciliação abortada: %s", res.abortada)
        return res

    estado_por_q = {q: e for q, e, _, _ in capturas if q}
    imagens_ref = {ic: cid for _, e, ic, cid in capturas if ic}

    orfaos_q = [n for n, idade in q_arqs.items() if n not in estado_por_q and idade > IDADE_ORFAO_S]
    finalizados_q = [n for n in q_arqs if estado_por_q.get(n) in ("processada", "recusada")]
    orfaos_im = [r for r, idade in im_arqs.items() if r not in imagens_ref and idade > IDADE_ORFAO_S]

    candidatos = len(orfaos_q) + len(orfaos_im)
    if candidatos > 200 or (candidatos > 10 and candidatos > 0.05 * max(total, 1)):   # (3)
        res.abortada = f"trava de segurança: {candidatos} candidatos de {total} arquivos"
        log.error("reconciliação abortada: %s", res.abortada)
        return res

    for n in orfaos_q:                                                          # (4)
        _para_lixeira(cfg, cfg.quarentena, cfg.quarentena / n, f"quarentena/{n}"); res.movidos_lixeira.append(f"quarentena/{n}")
    for r in orfaos_im:
        _para_lixeira(cfg, cfg.imagens, cfg.imagens / r, f"imagens/{r}"); res.movidos_lixeira.append(f"imagens/{r}")
    for n in finalizados_q:                                                     # (5)
        (cfg.quarentena / n).unlink(missing_ok=True); res.quarentena_apagada.append(n)

    ausentes = [cid for ic, cid in imagens_ref.items() if ic not in im_arqs and not (cfg.imagens / ic).exists()]
    if ausentes:                                                                # (6)
        with psycopg.connect(dsn_worker, autocommit=True) as c:
            for cid in ausentes:
                c.execute("SELECT op.registrar_imagem_ausente(%s)", (cid,))
        res.imagens_ausentes = ausentes

    limite = dt.date.today() - dt.timedelta(days=DIAS_LIXEIRA)
    for lixeira in (cfg.lixeira_de(cfg.quarentena), cfg.lixeira_de(cfg.imagens)):
        if not lixeira.is_dir():
            continue
        for d in lixeira.iterdir():
            try:
                dia = dt.datetime.strptime(d.name, "%Y%m%d").date()
            except ValueError:
                continue
            if dia < limite:
                shutil.rmtree(d); res.lixeira_esvaziada += 1
    return res
