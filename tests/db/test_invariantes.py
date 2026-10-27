"""Cada teste tenta violar uma invariante e verifica que o banco recusa.
Os nomes I-xx correspondem às invariantes listadas em docs/seguranca.md."""
import threading
import psycopg
import pytest
from psycopg import errors
from conftest import DSN, como, um


def falha(conn, sql, args=(), sqlstate=None, exc=psycopg.Error):
    """Executa dentro de um savepoint e exige erro (opcionalmente com SQLSTATE específico)."""
    with pytest.raises(exc) as e:
        with conn.transaction():
            conn.execute(sql, args)
    if sqlstate:
        assert e.value.sqlstate == sqlstate, (e.value.sqlstate, str(e.value))
    return e.value


def transicao(conn, alerta, versao, para, usuario, causa=None, motivo=None):
    return um(conn, "SELECT op.registrar_transicao(%s, %s, %s, %s, %s, %s)",
              alerta, versao, para, usuario, causa, motivo)


# I-01: Previsão do modelo nunca vira confirmação humana
def test_i01_worker_nao_executa_transicao(conn, cenario):
    como(conn, "stp_worker")
    falha(conn, "SELECT op.registrar_transicao(%s, 1, 'confirmado', %s, 'estoque_fantasma')",
          (cenario["alerta"], cenario["gerente"]), exc=errors.InsufficientPrivilege)


def test_i01_worker_nao_altera_status_diretamente(conn, cenario):
    como(conn, "stp_worker")
    falha(conn, "UPDATE op.alerta SET status = 'confirmado' WHERE id = %s",
          (cenario["alerta"],), exc=errors.InsufficientPrivilege)


def test_i01_worker_nao_insere_alerta_ja_confirmado(conn, cenario):
    como(conn, "stp_worker")
    falha(conn, "INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, versao_regra) VALUES (%s, %s, %s, 'V', %s, 'r1')",
          (cenario["loja"], cenario["posicao2"], cenario["produto2"], cenario["obs2"]), exc=errors.InsufficientPrivilege)
    conn.execute("RESET ROLE"); como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra, status)
                   VALUES (%s, %s, %s, 'D2', %s, %s, 'r1', 'confirmado')""",
          (cenario["loja"], cenario["posicao2"], cenario["produto2"], cenario["obs2"], cenario["snap2"]), sqlstate="STP02")


def test_i01_confirmacao_exige_gerente_e_causa(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "SELECT op.registrar_transicao(%s, 1, 'confirmado', %s, 'estoque_fantasma')",
          (cenario["alerta"], cenario["repositor"]), sqlstate="STP03")
    falha(conn, "SELECT op.registrar_transicao(%s, 1, 'confirmado', %s)",
          (cenario["alerta"], cenario["gerente"]), exc=errors.CheckViolation)
    assert transicao(conn, cenario["alerta"], 1, "confirmado", cenario["gerente"], causa="estoque_fantasma") == 2


# I-02: Alerta vinculado à observação e ao snapshot que o originaram
def test_i02_divergencia_exige_snapshot(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
                   VALUES (%s, %s, %s, 'D2', %s, NULL, 'r1')""",
          (cenario["loja"], cenario["posicao2"], cenario["produto2"], cenario["obs2"]), exc=errors.CheckViolation)


def test_i02_observacao_de_outra_posicao(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
                   VALUES (%s, %s, %s, 'D2', %s, %s, 'r1')""",
          (cenario["loja"], cenario["posicao2"], cenario["produto"], cenario["obs"], cenario["snap"]),
          exc=errors.ForeignKeyViolation)


def test_i02_snapshot_de_outro_produto(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
                   VALUES (%s, %s, %s, 'D2', %s, %s, 'r1')""",
          (cenario["loja"], cenario["posicao2"], cenario["produto2"], cenario["obs2"], cenario["snap"]),
          exc=errors.ForeignKeyViolation)


def test_i02_produto_diferente_do_planograma(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
                   VALUES (%s, %s, %s, 'D1', %s, %s, 'r1')""",
          (cenario["loja"], cenario["posicao2"], cenario["produto"], cenario["obs2"], cenario["snap"]), sqlstate="STP02")


def test_i02_evidencias_imutaveis_mesmo_para_dono(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "UPDATE op.alerta SET snapshot_id = NULL WHERE id = %s", (cenario["alerta"],), sqlstate="STP01")


# I-03: Importação idempotente
def test_i03_mesmo_arquivo_duas_vezes(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas) VALUES (%s, 'estoque', repeat('b', 64), '2026-10-20', 2, 0)",
          (cenario["loja"],), exc=errors.UniqueViolation)


def test_i03_produto_repetido_no_mesmo_arquivo(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, '2026-10-20', 5)",
          (cenario["imp"], cenario["produto"]), exc=errors.UniqueViolation)


def test_i03_quantidade_absurda(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, '2026-10-20', 1e9)",
          (cenario["imp"], cenario["produto2"]), exc=psycopg.errors.NumericValueOutOfRange)


def test_i03_reprocessar_captura_nao_duplica(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.5)",
          (cenario["captura"], cenario["posicao"], cenario["plan"], cenario["metodo"]), exc=errors.UniqueViolation)


def test_i03_mesma_foto_duas_vezes(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "INSERT INTO op.captura (loja_id, sha256_original, capturado_em) VALUES (%s, repeat('a', 64), now())",
          (cenario["loja"],), exc=errors.UniqueViolation)


# I-04: Planograma não reescreve o passado
def test_i04_vigencias_sobrepostas(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-06-01', NULL))",
          (cenario["posicao"], cenario["produto2"]), exc=errors.ExclusionViolation)


def test_i04_trocar_produto_de_vigencia_existente(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "UPDATE op.planograma_vigencia SET produto_id = %s WHERE id = %s",
          (cenario["produto2"], cenario["plan"]), sqlstate="STP01")


def test_i04_mudanca_correta_preserva_historico(conn, cenario):
    como(conn, "stp_migrator")
    conn.execute("UPDATE op.planograma_vigencia SET vigencia = tstzrange(lower(vigencia), '2026-11-01') WHERE id = %s", (cenario["plan"],))
    conn.execute("INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-11-01', NULL))",
                 (cenario["posicao"], cenario["produto2"]))
    produto_da_obs = um(conn, """SELECT pv.produto_id FROM op.observacao_visual o
                                 JOIN op.planograma_vigencia pv ON pv.id = o.planograma_id WHERE o.id = %s""", cenario["obs"])
    assert produto_da_obs == cenario["produto"]


# I-05: Concorrência não sobrescreve decisão
def test_i05_versao_desatualizada(conn, cenario):
    como(conn, "stp_app")
    transicao(conn, cenario["alerta"], 1, "em_verificacao", cenario["repositor"])
    falha(conn, "SELECT op.registrar_transicao(%s, 1, 'rejeitado', %s, NULL, 'produto_presente')",
          (cenario["alerta"], cenario["gerente"]), sqlstate="STP09")


def test_i05_decisao_final_nao_reverte(conn, cenario):
    como(conn, "stp_app")
    transicao(conn, cenario["alerta"], 1, "confirmado", cenario["gerente"], causa="reposicao_pendente")
    falha(conn, "SELECT op.registrar_transicao(%s, 2, 'rejeitado', %s, NULL, 'produto_presente')",
          (cenario["alerta"], cenario["admin"]), sqlstate="STP05")


def test_i05_status_nao_muda_fora_da_funcao(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, "UPDATE op.alerta SET status = 'rejeitado' WHERE id = %s", (cenario["alerta"],), sqlstate="STP01")


def test_i05_duas_sessoes_simultaneas():
    """Duas conexões reais tentam decidir o mesmo alerta ao mesmo tempo: uma vence, a outra recebe conflito."""
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute("SET ROLE stp_migrator")
        loja = um(setup, "INSERT INTO op.loja (nome, origem_dado) VALUES ('Concorrência', 'sintetico') RETURNING id")
        g1 = um(setup, "INSERT INTO op.usuario (loja_id, nome, papel) VALUES (%s, 'g1', 'gerente') RETURNING id", loja)
        g2 = um(setup, "INSERT INTO op.usuario (loja_id, nome, papel) VALUES (%s, 'g2', 'gerente') RETURNING id", loja)
        prod = um(setup, "INSERT INTO op.produto (loja_id, descricao) VALUES (%s, 'x') RETURNING id", loja)
        pos = um(setup, "INSERT INTO op.posicao (loja_id, codigo) VALUES (%s, 'P1') RETURNING id", loja)
        plan = um(setup, "INSERT INTO op.planograma_vigencia (posicao_id, produto_id, vigencia) VALUES (%s, %s, tstzrange('2026-01-01', NULL)) RETURNING id", pos, prod)
        met = um(setup, "INSERT INTO op.metodo_ocupacao (nome, versao, limiar) VALUES ('t', 'conc', 0.5) RETURNING id")
        cap = um(setup, "INSERT INTO op.captura (loja_id, sha256_original, capturado_em) VALUES (%s, repeat('c', 64), now()) RETURNING id", loja)
        obs = um(setup, "INSERT INTO op.observacao_visual (captura_id, posicao_id, planograma_id, metodo_id, classe, confianca) VALUES (%s, %s, %s, %s, 'vazio', 0.9) RETURNING id", cap, pos, plan, met)
        imp = um(setup, "INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas) VALUES (%s, 'estoque', repeat('d', 64), (now() AT TIME ZONE 'America/Fortaleza')::date, 1, 0) RETURNING id", loja)
        snap = um(setup, "INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade) VALUES (%s, %s, (now() AT TIME ZONE 'America/Fortaleza')::date, 10) RETURNING id", imp, prod)
        alerta = um(setup, "INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra) VALUES (%s, %s, %s, 'D1', %s, %s, 'r1') RETURNING id", loja, pos, prod, obs, snap)
    try:
        a = psycopg.connect(DSN); b = psycopg.connect(DSN)
        a.execute("SET ROLE stp_app"); b.execute("SET ROLE stp_app")
        # A decide e segura a transação aberta (com o lock da linha)
        a.execute("SELECT op.registrar_transicao(%s, 1, 'confirmado', %s, 'estoque_fantasma')", (alerta, g1))
        resultado = {}

        def sessao_b():
            try:
                b.execute("SELECT op.registrar_transicao(%s, 1, 'rejeitado', %s, NULL, 'produto_presente')", (alerta, g2))
                b.commit(); resultado["b"] = "ok"
            except psycopg.Error as e:
                b.rollback(); resultado["b"] = e.sqlstate

        t = threading.Thread(target=sessao_b); t.start()
        t.join(timeout=1.0)
        assert t.is_alive(), "B deveria estar bloqueada esperando o lock de A"
        a.commit(); t.join(timeout=5)
        assert resultado["b"] == "STP09"
        with psycopg.connect(DSN) as v:
            assert um(v, "SELECT status FROM op.alerta WHERE id = %s", alerta) == "confirmado"
            assert um(v, "SELECT count(*) FROM op.transicao_alerta WHERE alerta_id = %s", alerta) == 1
        a.close(); b.close()
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("SET ROLE stp_migrator"); c.execute("CALL op.excluir_loja(%s)", (loja,))
            c.execute("DELETE FROM op.metodo_ocupacao WHERE id = %s", (met,))


def test_i05_um_alerta_aberto_por_posicao_e_tipo(conn, cenario):
    como(conn, "stp_migrator")
    falha(conn, """INSERT INTO op.alerta (loja_id, posicao_id, produto_id, tipo, observacao_id, snapshot_id, versao_regra)
                   VALUES (%s, %s, %s, 'D1', %s, %s, 'r2')""",
          (cenario["loja"], cenario["posicao"], cenario["produto"], cenario["obs"], cenario["snap"]), exc=errors.UniqueViolation)


# I-06: Histórico somente inserção
@pytest.mark.parametrize("tabela", ["observacao_visual", "snapshot_estoque", "importacao"])
def test_i06_app_nao_altera_eventos(conn, cenario, tabela):
    como(conn, "stp_app")
    falha(conn, f"DELETE FROM op.{tabela}", exc=errors.InsufficientPrivilege)


@pytest.mark.parametrize("tabela", ["observacao_visual", "snapshot_estoque", "importacao", "transicao_alerta"])
def test_i06_nem_o_dono_apaga_eventos(conn, cenario, tabela):
    como(conn, "stp_app")
    transicao(conn, cenario["alerta"], 1, "em_verificacao", cenario["repositor"])
    conn.execute("RESET ROLE"); como(conn, "stp_migrator")
    falha(conn, f"DELETE FROM op.{tabela}", sqlstate="STP01")


def test_i06_captura_estado_final_e_origem_imutavel(conn, cenario):
    como(conn, "stp_worker")
    falha(conn, "UPDATE op.captura SET estado = 'recusada', motivo_recusa = 'x' WHERE id = %s", (cenario["captura"],),
          exc=errors.InsufficientPrivilege)
    conn.execute("RESET ROLE"); como(conn, "stp_migrator")
    conn.execute("UPDATE op.captura SET estado = 'processada', imagem_sha256 = repeat('9', 64) WHERE id = %s", (cenario["captura"],))
    falha(conn, "UPDATE op.captura SET estado = 'recusada', motivo_recusa = 'x' WHERE id = %s", (cenario["captura"],), sqlstate="STP01")
    falha(conn, "UPDATE op.captura SET imagem_sha256 = repeat('8', 64) WHERE id = %s", (cenario["captura"],), sqlstate="STP01")
    falha(conn, "UPDATE op.captura SET sha256_original = repeat('7', 64) WHERE id = %s", (cenario["captura"],), sqlstate="STP01")


# I-07: Leitura analítica: só leitura e só dados reais
def test_i07_bi_le_somente_views_reais(conn, cenario):
    como(conn, "stp_migrator")
    sint = um(conn, "INSERT INTO op.loja (nome, origem_dado) VALUES ('Demo', 'sintetico') RETURNING id")
    conn.execute("RESET ROLE"); como(conn, "stp_bi")
    lojas = {r[0] for r in conn.execute("SELECT loja_id FROM an.fato_alerta UNION SELECT loja_id FROM an.frescor_dados")}
    assert cenario["loja"] in lojas and sint not in lojas
    falha(conn, "SELECT * FROM op.alerta", exc=errors.InsufficientPrivilege)
    falha(conn, "SELECT * FROM op.usuario", exc=errors.InsufficientPrivilege)


def test_i07_bi_nao_escreve():
    with psycopg.connect(DSN) as c:
        c.execute("SET ROLE stp_bi")
        # o papel tem default_transaction_read_only; vale a partir da sessão que o assume no login.
        # Aqui verificamos o privilégio: nenhuma escrita possível.
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("INSERT INTO op.loja (nome, origem_dado) VALUES ('x', 'real')")


# I-08: Expurgo controlado
def test_i08_app_nao_expurga(conn, cenario):
    como(conn, "stp_app")
    falha(conn, "CALL op.excluir_loja(%s)", (cenario["loja"],), exc=errors.InsufficientPrivilege)


def test_i08_expurgo_remove_tudo_da_loja(conn, cenario):
    como(conn, "stp_app")
    transicao(conn, cenario["alerta"], 1, "confirmado", cenario["gerente"], causa="estoque_fantasma")
    conn.execute("RESET ROLE"); como(conn, "stp_migrator")
    conn.execute("CALL op.excluir_loja(%s)", (cenario["loja"],))
    for t in ("usuario", "produto", "posicao", "captura", "importacao", "alerta"):
        assert um(conn, f"SELECT count(*) FROM op.{t} WHERE loja_id = %s", cenario["loja"]) == 0
    assert um(conn, "SELECT count(*) FROM op.transicao_alerta WHERE alerta_id = %s", cenario["alerta"]) == 0
    assert um(conn, "SELECT current_setting('stp.expurgo', true)") == "off"


# I-09: Funções privilegiadas não ficam abertas a PUBLIC
def test_i09_nenhuma_funcao_security_definer_publica(conn):
    abertas = conn.execute("""
        SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'op' AND p.prosecdef
           AND has_function_privilege('public', p.oid, 'EXECUTE')""").fetchall()
    assert abertas == []
