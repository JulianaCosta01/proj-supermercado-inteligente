"""Processo do worker: python -m app.worker_cli  (método B1: marcadores ArUco + foto de referência)

Laço simples: processa capturas enquanto houver; dorme quando a fila esvazia;
roda a reconciliação a cada hora. Sem fila externa: a fila é a tabela op.captura.
"""
import argparse
import logging
import time

from app.config import Config
from app.reconciliacao import reconciliar
from app.visao import MetodoB1
from app.worker import Worker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metodo", choices=["b1"], default="b1", help="método de ocupação (b1: marcadores + referência)")
    ap.add_argument("--intervalo", type=float, default=5.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config()
    cfg.preparar_diretorios()
    metodo = MetodoB1(cfg.referencias)
    w = Worker(cfg, metodo)
    proxima_reconciliacao = 0.0
    try:
        while True:
            if time.time() >= proxima_reconciliacao:
                r = reconciliar(cfg, cfg.dsn_worker)
                logging.getLogger("stp.reconciliacao").info("resultado: %s", r)
                proxima_reconciliacao = time.time() + 3600
            if w.processar_um() is None:
                time.sleep(a.intervalo)
    finally:
        w.fechar()


if __name__ == "__main__":
    main()
