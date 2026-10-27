"""Importação de CSV: tudo ou nada, três classes de linha, idempotência, relatório neutralizado."""
import datetime as dt
import psycopg
import pytest
from conftest import DSN, entrar, um

HOJE = dt.date.today().isoformat()
CAB = "codigo;ean;descricao;quantidade;data\n"


def contar(loja):
    with psycopg.connect(DSN) as c:
        return (um(c, "SELECT count(*) FROM op.snapshot_estoque s JOIN op.importacao i ON i.id = s.importacao_id WHERE i.loja_id = %s", loja),
                um(c, "SELECT count(*) FROM op.importacao_tentativa WHERE loja_id = %s", loja))


def enviar(tc, corpo):
    return tc.post("/importacoes/estoque", content=corpo if isinstance(corpo, bytes) else corpo.encode())


def test_arquivo_valido(cliente, amb):
    entrar(cliente, amb, "analista")
    corpo = CAB + f"C0;7891000100103;Café;10;{HOJE}\nC1;;Açúcar;3,5;{HOJE}\nFORA;;Outro item da loja;7;{HOJE}\n"
    r = enviar(cliente, corpo)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["linhas_gravadas"] == 2 and j["linhas_ignoradas"] == 1 and j["codigos_ausentes"] == ["C2"]
    assert contar(amb["loja"]) == (2, 0)


def test_mesmo_arquivo_duas_vezes(cliente, amb):
    entrar(cliente, amb, "admin")
    corpo = CAB + f"C0;;x;1;{HOJE}\n"
    assert enviar(cliente, corpo).status_code == 201
    assert enviar(cliente, corpo).status_code == 409
    assert contar(amb["loja"]) == (1, 0)


@pytest.mark.parametrize("nome,corpo", [
    ("cabecalho errado", "cod;qtd\nC0;1\n"),
    ("colunas a mais", CAB + f"C0;;x;1;{HOJE};extra\n"),
    ("milhar ambíguo", CAB + f"C0;;x;1.234;{HOJE}\n"),
    ("quantidade texto", CAB + f"C0;;x;abc;{HOJE}\n"),
    ("quantidade enorme", CAB + f"C0;;x;99999999;{HOJE}\n"),
    ("duas datas", CAB + f"C0;;x;1;{HOJE}\nC1;;y;1;2026-01-01\n"),
    ("data futura", CAB + "C0;;x;1;2099-01-01\n"),
    ("data impossível", CAB + "C0;;x;1;2026-02-30\n"),
    ("código repetido", CAB + f"C0;;x;1;{HOJE}\nC0;;x;2;{HOJE}\n"),
    ("código repetido fora do cadastro", CAB + f"Z9;;x;1;{HOJE}\nZ9;;x;2;{HOJE}\n"),
    ("ean com letras", CAB + f"C0;78910A;x;1;{HOJE}\n"),
    ("código vazio", CAB + f";;x;1;{HOJE}\n"),
    ("caractere de controle", CAB + f"C\x070;;x;1;{HOJE}\n"),
    ("binário", b"codigo;ean;descricao;quantidade;data\n\x00\x01\x02"),
    ("sem linhas", CAB),
    ("aspas quebradas", CAB + f'C0;;"x;1;{HOJE}\n'),
    ("uma linha ruim entre boas", CAB + f"C0;;x;1;{HOJE}\nC1;;y;-;{HOJE}\nC2;;z;3;{HOJE}\n"),
])
def test_arquivo_invalido_nao_grava_nada(cliente, amb, nome, corpo):
    entrar(cliente, amb, "analista")
    antes = contar(amb["loja"])
    r = enviar(cliente, corpo)
    assert r.status_code == 422, (nome, r.text)
    assert r.json()["n_erros"] >= 1
    depois = contar(amb["loja"])
    assert depois[0] == antes[0], "nenhum snapshot pode ser gravado"
    assert depois[1] == antes[1] + 1, "uma tentativa registrada"


@pytest.mark.parametrize("nome,corpo", [
    ("latin-1", (CAB + f"C0;;Café com açúcar;2;{HOJE}\n").encode("latin-1")),
    ("utf-8 com BOM", ("﻿" + CAB + f"C0;;Café;2;{HOJE}\n").encode("utf-8")),
    ("data brasileira", (CAB + f"C0;;x;2;{dt.date.today():%d/%m/%Y}\n").encode()),
    ("decimal com vírgula e negativo", (CAB + f"C0;;x;-1,5;{HOJE}\n").encode()),
    ("linhas em branco e CRLF", (CAB.replace("\n", "\r\n") + f"\r\nC0;;x;2;{HOJE}\r\n\r\n").encode()),
    ("fórmula na descrição é só texto", (CAB + f'C0;;=HYPERLINK("http://x");2;{HOJE}\n').encode()),
])
def test_variacoes_aceitas(cliente, amb, nome, corpo):
    entrar(cliente, amb, "analista")
    r = enviar(cliente, corpo)
    assert r.status_code == 201, (nome, r.text)


def test_relatorio_csv_neutraliza_formulas(cliente, amb):
    entrar(cliente, amb, "analista")
    r = enviar(cliente, CAB + f'=CMD|calc;;x;=1+1;{HOJE}\n')
    tid = r.json()["tentativa_id"]
    rel = cliente.get(f"/importacoes/tentativas/{tid}/relatorio.csv")
    assert rel.status_code == 200
    linhas = rel.text.splitlines()
    assert any(";'=1+1" in l for l in linhas), rel.text
    assert not any(";=" in l for l in linhas[1:])


def test_relatorio_de_outra_loja(cliente, amb, amb_b):
    entrar(cliente, amb, "analista")
    tid = enviar(cliente, "x\n").json()["tentativa_id"]
    entrar(cliente, amb_b, "analista")
    assert cliente.get(f"/importacoes/tentativas/{tid}/relatorio.csv").status_code == 404


def test_arquivo_grande_demais(cliente, amb, cfg):
    entrar(cliente, amb, "analista")
    corpo = CAB + ("C0;;x;1;" + HOJE + "\n") * (cfg.max_csv_bytes // 19 + 100)
    assert enviar(cliente, corpo).status_code == 413
