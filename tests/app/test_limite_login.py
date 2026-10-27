"""Limite de tentativas de login: bloqueio, enumeração de contas, bloqueio indevido de terceiros e corrida."""
import threading
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from conftest import DSN, DSN_APP, SENHA, um
from app.api import criar_app


@pytest.fixture
def cliente_ip(cfg):
    """Fábrica de clientes com IP de origem próprio (cada teste usa IPs novos)."""
    abertos = []

    def fazer(ip=None):
        tc = TestClient(criar_app(cfg), client=(ip or f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}", 50000))
        tc.__enter__(); abertos.append(tc)
        return tc
    yield fazer
    for tc in abertos:
        tc.__exit__(None, None, None)


def tentar(tc, login, senha):
    return tc.post("/sessao", json={"login": login, "senha": senha})


def test_bloqueia_depois_de_cinco_falhas_e_nem_a_senha_certa_passa(cliente_ip, amb):
    tc, login = cliente_ip(), f"gerente-{amb['sufixo']}"
    assert [tentar(tc, login, "errada").status_code for _ in range(5)] == [401] * 5
    r = tentar(tc, login, "errada")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert tentar(tc, login, SENHA).status_code == 429          # sem oráculo de senha durante o bloqueio


def test_conta_inexistente_responde_igual(cliente_ip):
    tc = cliente_ip()
    codigos = [tentar(tc, "naoexiste-xyz", "x").status_code for _ in range(6)]
    assert codigos == [401] * 5 + [429]


def test_variacao_de_maiusculas_conta_junto(cliente_ip, amb):
    tc, login = cliente_ip(), f"gerente-{amb['sufixo']}"
    for i in range(5):
        tentar(tc, login.upper() if i % 2 else login, "errada")
    assert tentar(tc, login, SENHA).status_code == 429


def test_bloqueio_de_um_ip_nao_tranca_a_conta_para_outro(cliente_ip, amb):
    login = f"gerente-{amb['sufixo']}"
    atacante, dono = cliente_ip(), cliente_ip()
    for _ in range(6):
        tentar(atacante, login, "errada")
    assert tentar(atacante, login, SENHA).status_code == 429
    assert tentar(dono, login, SENHA).status_code == 200


def test_ataque_distribuido_bloqueia_a_conta(cliente_ip, amb):
    login = f"gerente-{amb['sufixo']}"
    for _ in range(5):                                   # 5 IPs x 4 falhas = 20, abaixo dos limites por IP
        tc = cliente_ip()
        assert all(tentar(tc, login, "errada").status_code == 401 for _ in range(4))
    assert tentar(cliente_ip(), login, SENHA).status_code == 429


def test_um_ip_varrendo_contas_e_bloqueado(cliente_ip):
    tc = cliente_ip()
    codigos = [tentar(tc, f"conta-{i:03d}", "x").status_code for i in range(31)]
    assert codigos[:30] == [401] * 30 and codigos[30] == 429


def test_acerto_zera_o_contador_da_conta_no_ip(cliente_ip, amb):
    tc, login = cliente_ip(), f"gerente-{amb['sufixo']}"
    for _ in range(4):
        tentar(tc, login, "errada")
    assert tentar(tc, login, SENHA).status_code == 200
    assert [tentar(tc, login, "errada").status_code for _ in range(4)] == [401] * 4


def test_bloqueio_expira(cliente_ip, amb):
    ip = "10.250.0.1"
    tc, login = cliente_ip(ip), f"gerente-{amb['sufixo']}"
    for _ in range(6):
        tentar(tc, login, "errada")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET ROLE stp_migrator")
        c.execute("UPDATE op.limite_login SET bloqueado_ate = now() - interval '1 second', janela_inicio = now() - interval '20 minutes' WHERE chave LIKE %s",
                  (f"%{ip}%",))
    assert tentar(tc, login, SENHA).status_code == 200
    assert tentar(tc, login, "errada").status_code == 401      # contagem recomeçou do zero


def test_tentativas_paralelas_nao_furam_o_limite():
    """30 conexões chamam ao mesmo tempo: só 5 podem seguir para a verificação de senha."""
    login, ip, liberadas, barreira = f"paralelo-{uuid.uuid4().hex[:6]}", "10.251.0.1", [], threading.Barrier(30)

    def uma():
        with psycopg.connect(DSN_APP, autocommit=True) as c:
            barreira.wait()
            if um(c, "SELECT op.tentar_login(%s, %s)", login, ip) == 0:
                liberadas.append(1)
    ts = [threading.Thread(target=uma) for _ in range(30)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert len(liberadas) == 5


def test_aplicacao_nao_le_nem_altera_os_contadores():
    with psycopg.connect(DSN_APP) as c:
        for sql in ("SELECT * FROM op.limite_login", "DELETE FROM op.limite_login", "UPDATE op.limite_login SET falhas = 0"):
            with pytest.raises(errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute(sql)
