"""F-xx: falhas que o banco aceitava antes da correção (cada teste reproduz uma).
W-xx: fila do worker com reserva curta."""
import threading
import time
import uuid
import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb
from conftest import DSN, como, um
from test_invariantes import falha


# F-01: versão NULL desligava o controle de concorrência
def test_f01_versao_nula_recusada(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "SELECT op.registrar_transicao(%s, NULL, 'em_verificacao', %s)",
          (cenario["alerta"], cenario["gerente"]), sqlstate="STP08")
    falha(conn, "SELECT op.registrar_transicao(%s, 1, NULL, %s)",
          (cenario["alerta"], cenario["gerente"]), sqlstate="STP08")


# F-02: observação apontava para vigência que não valia na captura
def test_f02_observacao_com_vigencia_fora_da_captura(conn, cenario):
    como(conn, "stp_migrator")
    pos = um(conn, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'X') RETURNING id", cenario["loja"])
    antiga = um(conn, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', '2026-02-01')) RETURNING id",
                pos, cenario["produto"])
    falha(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.9)",
          (cenario["captura"], pos, antiga, cenario["metodo"]), sqlstate="STP02")


def test_f02_encerrar_vigencia_antes_de_observacao_existente(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "UPDATE op.planograma_vigencia SET vigencia = tstzrange(lower(vigencia), '2026-10-01') WHERE id = %s",
          (cenario["plan"],), sqlstate="STP01")


# F-03: snapshot velho ou superado gerava divergência
def _snapshot(conn, loja, produto, data, sha):
    imp = um(conn, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas, importado_em) VALUES (%s, 'estoque', %s, %s, 1, 0, clock_timestamp()) RETURNING id",
             loja, sha, data)
    return um(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, %s, 10) RETURNING id",
              imp, produto, data)


def _alerta_d2(conn, c, snap):
    return ("INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra) VALUES (%s, %s, %s, 'D2', %s, %s, 'r1')",
            (c["loja"], c["posicao2"], c["produto2"], c["obs2"], snap))


def test_f03_snapshot_velho(conn, cenario):
    """Produto cujo único snapshot é antigo: só a janela de atualidade pode barrar."""
    como(conn, "stp_migrator")
    p3 = um(conn, "INSERT INTO op.produto (loja_id, descricao) VALUES (%s, 'p3') RETURNING id", cenario["loja"])
    pos3 = um(conn, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'P3') RETURNING id", cenario["loja"])
    pl3 = um(conn, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL)) RETURNING id", pos3, p3)
    ob3 = um(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.9) RETURNING id",
             cenario["captura"], pos3, pl3, cenario["metodo"])
    velho = _snapshot(conn, cenario["loja"], p3, "2026-10-10", "1" * 64)
    falha(conn, "INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra) VALUES (%s, %s, %s, 'D1', %s, %s, 'r1')",
          (cenario["loja"], pos3, p3, ob3, velho), sqlstate="STP07")


def test_f03_snapshot_do_futuro(conn, cenario):
    como(conn, "stp_migrator")
    futuro = _snapshot(conn, cenario["loja"], cenario["produto2"], "2026-10-21", "2" * 64)
    falha(conn, *_alerta_d2(conn, cenario, futuro), sqlstate="STP07")


def test_f03_snapshot_superado_por_outro_mais_recente(conn, cenario):
    como(conn, "stp_migrator")
    _snapshot(conn, cenario["loja"], cenario["produto2"], "2026-10-20", "3" * 64)  # correção do mesmo dia, importada depois
    falha(conn, *_alerta_d2(conn, cenario, cenario["snap2"]), sqlstate="STP07")


def test_f03_snapshot_do_dia_anterior_aceito(conn, cenario):
    como(conn, "stp_migrator")
    ontem = _snapshot(conn, cenario["loja"], cenario["produto2"], "2026-10-19", "4" * 64)
    # snap2 (dia 20) é mais recente; usar o de ontem deve falhar, usar o de hoje deve passar
    falha(conn, *_alerta_d2(conn, cenario, ontem), sqlstate="STP07")
    sql, args = _alerta_d2(conn, cenario, cenario["snap2"])
    conn.execute(sql, args)


def test_f03_arquivo_tem_uma_unica_data(conn, cenario):
    como(conn, "stp_migrator")
    p3 = um(conn, "INSERT INTO op.produto (loja_id, descricao) VALUES (%s, 'p3') RETURNING id", cenario["loja"])
    conn.execute("RESET ROLE"); como(conn, "stp_app")
    falha(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, '2026-10-21', 1)",
          (cenario["imp"], p3), exc=errors.ForeignKeyViolation)


# F-04: papéis da aplicação podiam criar objetos temporários
@pytest.mark.parametrize("papel", ["stp_app", "stp_worker", "stp_bi"])
def test_f04_sem_objetos_temporarios(conn, papel):
    como(conn, papel)
    falha(conn, "CREATE TEMP TABLE alerta (id int)", exc=errors.InsufficientPrivilege)


@pytest.mark.parametrize("papel", ["stp_app", "stp_worker", "stp_bi"])
def test_f04_nenhum_schema_gravavel(conn, papel):
    gravaveis = conn.execute("""SELECT nspname FROM pg_namespace
                                 WHERE has_schema_privilege(%s, oid, 'CREATE')""", (papel,)).fetchall()
    assert gravaveis == []


# F-05: função nova nascia executável por PUBLIC
def test_f05_funcao_nova_nasce_fechada(conn):
    como(conn, "stp_migrator")
    conn.execute("CREATE FUNCTION op.teste_nova() RETURNS int LANGUAGE sql SET search_path = pg_catalog, pg_temp AS 'select 1'")
    assert um(conn, "SELECT has_function_privilege('public', 'op.teste_nova()', 'EXECUTE')") is False


# F-06: funções sem search_path fixo
def test_f06_toda_funcao_fixa_search_path(conn):
    soltas = conn.execute("""
        SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname IN ('op', 'an')
           AND NOT coalesce('search_path=pg_catalog, pg_temp' = ANY (p.proconfig), false)""").fetchall()
    assert soltas == []


def test_f06_search_path_do_chamador_nao_interfere(conn, cenario):
    como(conn, "stp_app")
    conn.execute("SET LOCAL search_path = pg_temp, public")
    assert um(conn, "SELECT op.registrar_transicao(%s, 1, 'em_verificacao', %s)", cenario["alerta"], cenario["repositor"]) == 2


# F-07: loja com autorização revogada
def test_f07_loja_revogada(conn, cenario):
    como(conn, "stp_migrator")
    conn.execute("UPDATE op.loja SET autorizacao_revogada_em = now() WHERE id = %s", (cenario["loja"],))
    conn.execute("RESET ROLE"); como(conn, "stp_app")
    falha(conn, "INSERT INTO op.captura (loja_id, sha256_original, capturado_em) VALUES (%s, repeat('9', 64), now())",
          (cenario["loja"],), sqlstate="STP06")
    falha(conn, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas) VALUES (%s, 'estoque', repeat('9', 64), current_date, 1, 0)",
          (cenario["loja"],), sqlstate="STP06")
    conn.execute("RESET ROLE"); como(conn, "stp_bi")
    assert um(conn, "SELECT count(*) FROM an.fato_alerta WHERE loja_id = %s", cenario["loja"]) == 0


# F-08: importação: tentativa rejeitada fica registrada, sem snapshot
def test_f08_tentativa_rejeitada_nao_grava_snapshot(conn, cenario):
    como(conn, "stp_app")
    conn.execute("""INSERT INTO op.importacao_tentativa (loja_id, tipo, sha256, usuario_id, n_erros, relatorio)
                    VALUES (%s, 'estoque', repeat('5', 64), %s, 2, %s)""",
                 (cenario["loja"], cenario["admin"], Jsonb([{"linha": 7, "erro": "quantidade inválida"}])))
    falha(conn, "INSERT INTO op.importacao_tentativa (loja_id, tipo, sha256, usuario_id, n_erros, relatorio) VALUES (%s, 'estoque', repeat('5', 64), %s, 0, '[]')",
          (cenario["loja"], cenario["admin"]), exc=errors.CheckViolation)
    falha(conn, "DELETE FROM op.importacao_tentativa", exc=errors.InsufficientPrivilege)


# Os testes W-xx usam dados confirmados e várias conexões, por isso removem a loja ao final.
@pytest.fixture
def fila():
    with psycopg.connect(DSN, autocommit=True) as s:
        s.execute("SET ROLE stp_migrator")
        loja = um(s, "INSERT INTO op.loja (nome, origem_dado) VALUES ('Fila', 'sintetico') RETURNING id")
        prod = um(s, "INSERT INTO op.produto (loja_id, descricao) VALUES (%s, 'p') RETURNING id", loja)
        pos = um(s, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'P1') RETURNING id", loja)
        s.execute("INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL))", (pos, prod))
        met = um(s, "INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES ('fila', %s, 0.5) RETURNING id", str(uuid.uuid4()))
        caps = [um(s, "INSERT INTO op.captura (loja_id, sha256_original, quarentena_chave, capturado_em) VALUES (%s, %s, gen_random_uuid(), now()) RETURNING id",
                   loja, f"{i:064x}") for i in range(1, 4)]
    yield {"loja": loja, "pos": pos, "met": met, "caps": caps}
    with psycopg.connect(DSN, autocommit=True) as s:
        s.execute("SET ROLE stp_migrator")
        s.execute("CALL op.excluir_loja(%s)", (loja,))
        s.execute("DELETE FROM op.metodo_ocupacao WHERE id = %s", (met,))


def worker():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute("SET ROLE stp_worker")
    return c


def reivindicar(c, lease="5 minutes"):
    return c.execute("SELECT * FROM op.reivindicar_captura('w', %s::interval)", (lease,)).fetchone()


def concluir(c, cap, token, f, alertas=None):
    obs = Jsonb([{"posicao_id": f["pos"], "classe": "ocupado", "confianca": 0.9}])
    c.execute("SELECT op.concluir_captura(%s, %s, %s, %s, %s, %s, %s)",
              (cap, token, None, "a" * 64, f["met"], obs, Jsonb(alertas) if alertas is not None else None))


def test_w01_reserva_nao_segura_lock(fila):
    """A transação de reserva termina antes do processamento: nenhuma trava fica presa."""
    w = worker()
    cap, token, *_ = reivindicar(w)
    with psycopg.connect(DSN) as outro:
        outro.execute("SELECT 1 FROM op.captura WHERE id = %s FOR UPDATE NOWAIT", (cap,))  # não bloqueia
    w.close()


def test_w02_workers_concorrentes_pegam_capturas_diferentes(fila):
    a = psycopg.connect(DSN); b = psycopg.connect(DSN)
    a.execute("SET ROLE stp_worker"); b.execute("SET ROLE stp_worker")
    ra = a.execute("SELECT captura_id FROM op.reivindicar_captura('a', '5 minutes')").fetchone()[0]
    rb = b.execute("SELECT captura_id FROM op.reivindicar_captura('b', '5 minutes')").fetchone()[0]  # A ainda não confirmou
    assert ra != rb
    a.commit(); b.commit(); a.close(); b.close()


def test_w03_reserva_vencida_volta_e_worker_atrasado_e_recusado(fila):
    w1, w2 = worker(), worker()
    cap, t1, *_ = reivindicar(w1, "1 second")
    time.sleep(1.2)                                   # w1 "travou" ou reiniciou
    cap2, t2, _, tentativa = reivindicar(w2)
    assert cap2 == cap and t2 != t1 and tentativa == 2
    with pytest.raises(psycopg.Error) as e:
        concluir(w1, cap, t1, fila)                   # w1 acorda tarde
    assert e.value.sqlstate == "STP10"
    concluir(w2, cap, t2, fila)
    assert um(w2, "SELECT count(*) FROM op.observacao_visual WHERE captura_id = %s", cap) == 1
    assert um(w2, "SELECT estado FROM op.captura WHERE id = %s", cap) == "processada"
    w1.close(); w2.close()


def test_w04_conclusao_e_atomica(fila):
    w = worker()
    cap, token, *_ = reivindicar(w)
    with pytest.raises(psycopg.Error) as e:   # alerta para posição sem observação: tudo é desfeito
        concluir(w, cap, token, fila, alertas=[{"posicao_id": fila["pos"] + 999, "tipo": "V", "snapshot_id": None, "versao_regra": "r1"}])
    assert e.value.sqlstate == "STP02"
    assert um(w, "SELECT count(*) FROM op.observacao_visual WHERE captura_id = %s", cap) == 0
    assert um(w, "SELECT estado FROM op.captura WHERE id = %s", cap) == "em_processamento"
    concluir(w, cap, token, fila, alertas=[{"posicao_id": fila["pos"], "tipo": "V", "snapshot_id": None, "versao_regra": "r1"}])
    assert um(w, "SELECT count(*) FROM op.alerta a JOIN op.observacao_visual o ON o.id = a.observacao_id WHERE o.captura_id = %s", cap) == 1
    w.close()


def test_w05_item_venenoso_vira_recusado(fila):
    w = worker()
    alvo = fila["caps"][0]
    for _ in range(3):
        cap, *_ = reivindicar(w, "1 second")
        assert cap == alvo
        time.sleep(1.1)                               # worker morre sem concluir
    proximo = reivindicar(w)
    assert proximo[0] != alvo
    assert um(w, "SELECT estado || ':' || motivo_recusa FROM op.captura WHERE id = %s", alvo) == "recusada:falha_processamento"
    w.close()


def test_w06_recusar_exige_token(fila):
    w = worker()
    cap, token, *_ = reivindicar(w)
    with pytest.raises(psycopg.Error) as e:
        w.execute("SELECT op.recusar_captura(%s, %s, 'formato invalido')", (cap, uuid.uuid4()))
    assert e.value.sqlstate == "STP10"
    w.execute("SELECT op.recusar_captura(%s, %s, 'formato invalido')", (cap, token))
    w.close()


def test_w07_worker_nao_escreve_direto(conn, cenario):
    como(conn, "stp_worker")
    falha(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.5)",
          (cenario["captura"], cenario["posicao2"], cenario["plan2"], cenario["metodo"]), exc=errors.InsufficientPrivilege)
    falha(conn, "UPDATE op.captura SET estado = 'processada' WHERE id = %s", (cenario["captura"],), exc=errors.InsufficientPrivilege)


def test_w08_worker_nao_grava_em_outra_loja(fila):
    """Observação para posição de outra loja é recusada mesmo com token válido."""
    w = worker()
    cap, token, *_ = reivindicar(w)
    with psycopg.connect(DSN, autocommit=True) as s:
        s.execute("SET ROLE stp_migrator")
        outra = um(s, "INSERT INTO op.loja (nome, origem_dado) VALUES ('Outra', 'sintetico') RETURNING id")
        p2 = um(s, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'Z') RETURNING id", outra)
    try:
        with pytest.raises(psycopg.Error) as e:
            w.execute("SELECT op.concluir_captura(%s, %s, NULL, %s, %s, %s, NULL)",
                      (cap, token, "a" * 64, fila["met"], Jsonb([{"posicao_id": p2, "classe": "vazio", "confianca": 0.9}])))
        assert e.value.sqlstate == "STP02"
    finally:
        with psycopg.connect(DSN, autocommit=True) as s:
            s.execute("SET ROLE stp_migrator"); s.execute("CALL op.excluir_loja(%s)", (outra,))
        w.close()
