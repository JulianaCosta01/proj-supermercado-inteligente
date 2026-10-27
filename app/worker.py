"""Worker de visão: reserva curta -> processamento fora de transação -> conclusão atômica.

Garantias (testadas em tests/app/test_worker.py):
  * nenhuma transação fica aberta durante a decodificação ou a classificação
  * só quem tem o token atual grava o resultado (worker atrasado é descartado)
  * reserva vencida volta para a fila; após 3 tentativas a captura vira recusada
  * imagem inválida é recusada sem nova tentativa; falha inesperada aguarda nova tentativa
  * a quarentena só é apagada depois do commit
"""
import datetime as dt
import json
import logging
import os
import resource
import subprocess
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from app import regras
from app.metodos import ImagemInadequada
from app.config import Config

log = logging.getLogger("stp.worker")
LEASE = "5 minutes"
TEMPO_LIMITE_S = 60
MEMORIA_LIMITE = 1536 * 1024 * 1024


def _limites():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORIA_LIMITE, MEMORIA_LIMITE))
    resource.setrlimit(resource.RLIMIT_CPU, (TEMPO_LIMITE_S, TEMPO_LIMITE_S))


def recodificar_isolado(entrada, saida, tempo_limite=TEMPO_LIMITE_S):
    """Retorna ('ok', info) | ('invalida', motivo) | ('falha', motivo)."""
    try:
        r = subprocess.run([sys.executable, "-E", "-s", "-m", "app.imagem", str(entrada), str(saida)],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           capture_output=True, text=True, timeout=tempo_limite, preexec_fn=_limites,
                           env={"PATH": os.environ.get("PATH", "")})
    except subprocess.TimeoutExpired:
        return "falha", "tempo limite"
    if r.returncode == 0:
        return "ok", json.loads(r.stdout)
    if r.returncode == 2:
        return "invalida", json.loads(r.stdout or '{"erro": "imagem inválida"}')["erro"]
    return "falha", f"código {r.returncode}"


class Worker:
    def __init__(self, cfg: Config, metodo, nome: str | None = None):
        self.cfg, self.metodo = cfg, metodo
        self.nome = nome or f"w-{uuid.uuid4().hex[:8]}"
        self.conn = psycopg.connect(cfg.dsn_worker, autocommit=True,
                                    options="-c statement_timeout=10000 -c idle_in_transaction_session_timeout=15000")
        r = self.conn.execute("SELECT id, limiar FROM op.metodo_ocupacao WHERE nome = %s AND versao = %s",
                              (metodo.nome, metodo.versao)).fetchone()
        if r is None:
            raise RuntimeError(f"método {metodo.nome} {metodo.versao} não registrado no banco")
        self.metodo_id, self.limiar = r[0], float(r[1])

    def fechar(self):
        self.conn.close()

    # ------------------------------------------------------------------ ciclo
    def processar_um(self, lease: str = LEASE) -> str | None:
        r = self.conn.execute("SELECT * FROM op.reivindicar_captura(%s, %s::interval)", (self.nome, lease)).fetchone()
        if r is None:
            return None
        captura_id, token, chave_q, tentativa = r
        quarentena = self.cfg.quarentena / str(chave_q) if chave_q else None
        if quarentena is None or not quarentena.exists():
            self._recusar(captura_id, token, "arquivo_ausente", None)
            return "recusada"

        saida_tmp = self.cfg.tmp_imagens / f"{uuid.uuid4()}.jpg"
        status, info = recodificar_isolado(quarentena, saida_tmp)
        if status == "invalida":
            saida_tmp.unlink(missing_ok=True)
            self._recusar(captura_id, token, f"imagem_invalida: {info}"[:200], quarentena)
            return "recusada"
        if status == "falha":
            saida_tmp.unlink(missing_ok=True)
            log.warning("captura %s tentativa %s falhou: %s", captura_id, tentativa, info)
            return "falha"            # a reserva vence e a captura volta para a fila

        sha = info["sha256"]
        rel = f"{sha[:2]}/{sha}.jpg"
        destino = self.cfg.imagens / rel
        destino.parent.mkdir(exist_ok=True)
        with open(saida_tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(saida_tmp, destino)

        try:
            captura, posicoes = self._contexto(captura_id)
            try:
                obs = self.metodo.classificar(destino, captura, posicoes)
            except ImagemInadequada as e:
                self._recusar(captura_id, token, f"imagem_inadequada: {e.motivo}"[:200], quarentena)
                return "recusada"
            alin = getattr(self.metodo, "ultimo_alinhamento", None)
            # Foto inteira inadequada (ex.: tremida, marcadores insuficientes): registra as observações
            # inconclusivas e a situação da foto, mas não abre alertas; a planta mostra "foto inadequada".
            alertas = [] if (alin is not None and alin.situacao != "alinhada") else self._alertas(captura, posicoes, obs)
            obs_json = Jsonb([{"posicao_id": p, "classe": c, "confianca": conf} for p, c, conf in obs])
            if alin is None:
                self.conn.execute("SELECT op.concluir_captura(%s, %s, %s, %s, %s, %s, %s)",
                                  (captura_id, token, rel, sha, self.metodo_id, obs_json, Jsonb(alertas)))
            else:
                self.conn.execute("SELECT op.concluir_captura(%s::bigint, %s::uuid, %s::text, %s::text, %s::bigint, %s::jsonb, %s::jsonb, "
                                  "%s::bigint, %s::text, %s::smallint, %s::numeric, %s::jsonb)",
                                  (captura_id, token, rel, sha, self.metodo_id, obs_json, Jsonb(alertas),
                                   alin.face_id, alin.situacao, alin.marcadores, alin.erro_mm, Jsonb(alin.metricas)))
        except psycopg.Error as e:
            if e.sqlstate == "STP10":
                log.info("captura %s: reserva perdida, resultado descartado", captura_id)
                return "descartada"
            log.warning("captura %s: erro ao concluir (%s), nova tentativa depois", captura_id, e.sqlstate)
            return "falha"
        quarentena.unlink(missing_ok=True)       # só depois do commit
        return "processada"

    def _recusar(self, captura_id, token, motivo, quarentena):
        try:
            self.conn.execute("SELECT op.recusar_captura(%s, %s, %s)", (captura_id, token, motivo))
        except psycopg.Error as e:
            if e.sqlstate != "STP10":
                raise
            return
        if quarentena is not None:
            quarentena.unlink(missing_ok=True)

    # ------------------------------------------------------------------ fatos para a regra
    def _contexto(self, captura_id):
        c = self.conn.execute("""SELECT c.id, c.loja_id, c.capturado_em, (c.capturado_em AT TIME ZONE l.fuso)::date, l.max_idade_snapshot_dias
                                   FROM op.captura c JOIN op.loja l ON l.id = c.loja_id WHERE c.id = %s""", (captura_id,)).fetchone()
        captura = {"id": c[0], "loja_id": c[1], "capturado_em": c[2], "dia": c[3], "max_idade": c[4], "faces": {}}
        for fid, larg, alt, dic, tam, ref in self.conn.execute(
                """SELECT id, largura_mm, altura_mm, aruco_dicionario, marcador_tamanho_mm, referencia_sha256
                     FROM op.face_gondola WHERE loja_id = %s""", (c[1],)).fetchall():
            captura["faces"][fid] = {"largura_mm": larg, "altura_mm": alt, "aruco_dicionario": dic,
                                     "marcador_tamanho_mm": tam, "referencia_sha256": ref, "marcadores": []}
        for fid, mid, x, y in self.conn.execute(
                "SELECT face_id, marcador_id, x_mm, y_mm FROM op.face_marcador WHERE loja_id = %s", (c[1],)).fetchall():
            captura["faces"][fid]["marcadores"].append({"id": mid, "x_mm": x, "y_mm": y})
        posicoes = [{"posicao_id": p, "codigo": cod, "produto_id": prod, "inicio_vigencia": ini,
                     "face_id": f, "x_mm": x, "y_mm": y, "largura_mm": w, "altura_mm": h}
                    for p, cod, prod, ini, f, x, y, w, h in self.conn.execute(
                        """SELECT p.id, p.codigo, pv.produto_id, lower(pv.vigencia), p.face_id, p.x_mm, p.y_mm, p.largura_mm, p.altura_mm
                             FROM op.posicao p
                             JOIN op.planograma_vigencia pv ON pv.posicao_id = p.id AND pv.vigencia @> %s
                            WHERE p.loja_id = %s""", (c[2], c[1])).fetchall()]
        return captura, posicoes

    def _alertas(self, captura, posicoes, obs):
        por_posicao = {p["posicao_id"]: p for p in posicoes}
        alertas = []
        for posicao_id, classe, conf in obs:
            pos = por_posicao.get(posicao_id)
            if pos is None:
                continue                               # o banco recusa a observação de qualquer forma
            snap = self.conn.execute(
                """SELECT s.id, s.data_referencia, s.quantidade FROM op.snapshot_estoque s
                     JOIN op.importacao i ON i.id = s.importacao_id
                    WHERE s.produto_id = %s AND s.data_referencia <= %s
                    ORDER BY s.data_referencia DESC, i.importado_em DESC LIMIT 1""",
                (pos["produto_id"], captura["dia"])).fetchone()
            pendente = self.conn.execute(
                """SELECT EXISTS (SELECT 1 FROM op.transicao_alerta t JOIN op.alerta a ON a.id = t.alerta_id
                                   WHERE a.posicao_id = %s AND t.motivo = 'planograma_errado' AND t.em >= %s)""",
                (posicao_id, pos["inicio_vigencia"])).fetchone()[0]
            f = regras.Fatos(classe=classe, confianca=conf, limiar=self.limiar, dia_captura=captura["dia"],
                             max_idade_dias=captura["max_idade"],
                             snapshot_id=snap[0] if snap else None, snapshot_data=snap[1] if snap else None,
                             quantidade=snap[2] if snap else None, planograma_pendente=pendente)
            res = regras.avaliar(f)
            if res:
                alertas.append({"posicao_id": posicao_id, "tipo": res[0], "snapshot_id": res[1],
                                "versao_regra": regras.VERSAO})
        return alertas
