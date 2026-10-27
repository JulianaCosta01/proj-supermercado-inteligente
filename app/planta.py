"""Dados da planta 2D: uma consulta por loja, estado visual derivado no servidor.

Estados (nunca chamamos de "ruptura" algo que uma pessoa não confirmou):
  divergencia_confirmada  alerta D1/D2 confirmado por pessoa (ainda não resolvido)
  possivel_divergencia    alerta D1/D2 aberto ou em verificação
  inconclusivo            alerta V aberto, observação indeterminada, ou foto da face inadequada
  sem_observacao          sem foto recente (ou nunca fotografada)
  vazio_coerente          vazio na foto e sem estoque no sistema
  parcial                 parcialmente abastecida
  ok                      ocupada, sem pendência
"""
import datetime as dt

HORAS_OBSERVACAO_RECENTE = 6

SQL = """
WITH ult_obs AS (
  SELECT DISTINCT ON (o.posicao_id) o.posicao_id, o.classe, o.confianca, c.capturado_em, c.id AS captura_id
    FROM op.observacao_visual o JOIN op.captura c ON c.id = o.captura_id
   WHERE c.loja_id = %(loja)s
   ORDER BY o.posicao_id, c.capturado_em DESC, o.id DESC
), ult_alerta AS (
  SELECT DISTINCT ON (a.posicao_id) a.posicao_id, a.id, a.tipo, a.status, a.versao, a.criado_em
    FROM op.alerta a
   WHERE a.loja_id = %(loja)s AND a.status IN ('aberto', 'em_verificacao', 'confirmado')
   ORDER BY a.posicao_id, (a.tipo = 'V'), a.criado_em DESC
), ult_snap AS (
  SELECT DISTINCT ON (s.produto_id) s.produto_id, s.quantidade, s.data_referencia
    FROM op.snapshot_estoque s JOIN op.importacao i ON i.id = s.importacao_id
   WHERE i.loja_id = %(loja)s
   ORDER BY s.produto_id, s.data_referencia DESC, i.importado_em DESC
), ult_face AS (
  SELECT DISTINCT ON (cf.face_id) cf.face_id, cf.situacao, cf.erro_alinhamento_mm, c.capturado_em, c.id AS captura_id
    FROM op.captura_face cf JOIN op.captura c ON c.id = cf.captura_id
   WHERE c.loja_id = %(loja)s
   ORDER BY cf.face_id, c.capturado_em DESC
)
SELECT f.id, f.codigo, f.largura_mm, f.altura_mm,
       uf.situacao, uf.erro_alinhamento_mm, uf.capturado_em, uf.captura_id,
       p.id, p.codigo, p.x_mm, p.y_mm, p.largura_mm, p.altura_mm,
       pr.id, pr.descricao,
       uo.classe, uo.confianca, uo.capturado_em,
       us.quantidade, us.data_referencia,
       ua.id, ua.tipo, ua.status, ua.versao
  FROM op.face_gondola f
  JOIN op.posicao p ON p.face_id = f.id
  LEFT JOIN op.planograma_vigencia pv ON pv.posicao_id = p.id AND pv.vigencia @> now()
  LEFT JOIN op.produto pr ON pr.id = pv.produto_id
  LEFT JOIN ult_face uf ON uf.face_id = f.id
  LEFT JOIN ult_obs uo ON uo.posicao_id = p.id
  LEFT JOIN ult_snap us ON us.produto_id = pr.id
  LEFT JOIN ult_alerta ua ON ua.posicao_id = p.id
 WHERE f.loja_id = %(loja)s
 ORDER BY f.codigo, p.y_mm, p.x_mm
"""


def estado_visual(obs_classe, obs_quando, alerta_tipo, alerta_status, face_situacao, quantidade, agora):
    if alerta_tipo in ("D1", "D2") and alerta_status == "confirmado":
        return "divergencia_confirmada"
    if alerta_tipo in ("D1", "D2"):
        return "possivel_divergencia"
    if obs_quando is None or agora - obs_quando > dt.timedelta(hours=HORAS_OBSERVACAO_RECENTE):
        return "sem_observacao"
    if alerta_tipo == "V" or obs_classe == "indeterminado" or (face_situacao not in (None, "alinhada")):
        return "inconclusivo"
    if obs_classe == "vazio":
        return "vazio_coerente" if quantidade is not None and quantidade <= 0 else "inconclusivo"
    if obs_classe == "parcial":
        return "parcial"
    return "ok"


def montar(conn, loja_id: int, agora=None) -> list[dict]:
    agora = agora or dt.datetime.now(dt.timezone.utc)
    faces = {}
    for r in conn.execute(SQL, {"loja": loja_id}).fetchall():
        (fid, fcod, fl, fa, fsit, ferr, fquando, fcap, pid, pcod, x, y, w, h, prid, prdesc,
         oclasse, oconf, oquando, qtd, qdata, aid, atipo, astatus, aversao) = r
        face = faces.setdefault(fid, {
            "id": fid, "codigo": fcod, "largura_mm": fl, "altura_mm": fa,
            "ultima_foto": None if fquando is None else {
                "captura_id": fcap, "capturado_em": fquando.isoformat(), "situacao": fsit,
                "erro_alinhamento_mm": None if ferr is None else float(ferr)},
            "posicoes": []})
        face["posicoes"].append({
            "id": pid, "codigo": pcod, "x_mm": x, "y_mm": y, "largura_mm": w, "altura_mm": h,
            "produto": None if prid is None else {"id": prid, "descricao": prdesc},
            "observacao": None if oclasse is None else {"classe": oclasse, "confianca": float(oconf), "capturado_em": oquando.isoformat()},
            "estoque": None if qtd is None else {"quantidade": float(qtd), "data_referencia": qdata.isoformat()},
            "alerta": None if aid is None else {"id": aid, "tipo": atipo, "status": astatus, "versao": aversao},
            "estado": estado_visual(oclasse, oquando, atipo, astatus, fsit, qtd, agora)})
    return list(faces.values())
