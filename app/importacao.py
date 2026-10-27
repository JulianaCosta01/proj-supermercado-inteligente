"""Importação de estoque por CSV (formato canônico do MVP).

Cabeçalho exato: codigo;ean;descricao;quantidade;data
Classes de linha:
  válida e mapeada  -> gravada
  código sem mapeamento em op.codigo_externo -> ignorada (fora do piloto), não é erro
  inválida          -> erro; QUALQUER erro rejeita o arquivo inteiro
A validação termina antes de qualquer escrita; o arquivo aceito é gravado numa única transação.
"""
import csv
import datetime as dt
import hashlib
import io
import re
from decimal import Decimal

from psycopg.types.json import Jsonb

CABECALHO = ["codigo", "ean", "descricao", "quantidade", "data"]
RE_QTD = re.compile(r"^-?\d{1,7}(,\d{1,3})?$")
RE_EAN = re.compile(r"^\d{8,14}$")
RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_BR = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_ERROS_RELATORIO = 1000


def _decodificar(dados: bytes) -> str:
    try:
        return dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dados.decode("latin-1")   # exportações antigas em Windows-1252/Latin-1


def _data(txt):
    if RE_ISO.match(txt):
        return dt.date.fromisoformat(txt)
    if RE_BR.match(txt):
        d, m, a = txt.split("/")
        return dt.date(int(a), int(m), int(d))
    raise ValueError


def validar(dados: bytes, max_linhas: int):
    """Retorna (linhas_validas, data_referencia, erros). Não toca no banco."""
    erros = []

    def erro(linha, campo, msg, valor=""):
        if len(erros) < MAX_ERROS_RELATORIO:
            erros.append({"linha": linha, "campo": campo, "erro": msg, "valor": str(valor)[:100]})

    if b"\x00" in dados:
        return [], None, [{"linha": 0, "campo": "arquivo", "erro": "arquivo binário", "valor": ""}]
    texto = _decodificar(dados)
    try:
        leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=";", strict=True)
        cab = next(leitor, None)
    except csv.Error as e:
        return [], None, [{"linha": 1, "campo": "arquivo", "erro": f"CSV mal formado: {e}", "valor": ""}]
    if cab is None or [c.strip().lower() for c in cab] != CABECALHO:
        return [], None, [{"linha": 1, "campo": "cabecalho", "erro": "cabeçalho diferente do esperado", "valor": ";".join(cab or [])}]

    validas, vistos, datas = [], {}, set()
    try:
        for n, campos in enumerate(leitor, start=2):
            if n - 1 > max_linhas:
                erro(n, "arquivo", f"mais de {max_linhas} linhas")
                break
            if not campos or all(not c.strip() for c in campos):
                continue
            if len(campos) != 5:
                erro(n, "linha", "número de colunas diferente de 5", ";".join(campos)); continue
            codigo, ean, _desc, qtd, data = (c.strip() for c in campos)
            ok = True
            if not (1 <= len(codigo) <= 40) or RE_CONTROLE.search(codigo):
                erro(n, "codigo", "código vazio, longo demais ou com caractere de controle", codigo); ok = False
            elif codigo in vistos:
                erro(n, "codigo", f"código repetido (também na linha {vistos[codigo]})", codigo); ok = False
            else:
                vistos[codigo] = n
            if ean and not RE_EAN.match(ean):
                erro(n, "ean", "EAN deve ter de 8 a 14 dígitos", ean); ok = False
            if not RE_QTD.match(qtd):
                erro(n, "quantidade", "use número com vírgula decimal, sem separador de milhar (ex.: 12 ou 3,5)", qtd); ok = False
            try:
                d = _data(data)
                if d > dt.date.today() + dt.timedelta(days=1):
                    raise ValueError
                datas.add(d)
            except ValueError:
                erro(n, "data", "data inválida ou no futuro (AAAA-MM-DD ou DD/MM/AAAA)", data); ok = False
            if ok:
                validas.append((codigo, Decimal(qtd.replace(",", ".")), n))
    except csv.Error as e:
        erro(0, "arquivo", f"CSV mal formado: {e}")
    if len(datas) > 1:
        erro(0, "data", "o arquivo deve ter uma única data de referência", ", ".join(sorted(map(str, datas))))
    if not erros and not validas:
        erro(0, "arquivo", "nenhuma linha de dados")
    return validas, (datas.pop() if len(datas) == 1 else None), erros


def importar_estoque(conn, loja_id: int, usuario_id: int, dados: bytes, max_linhas: int) -> dict:
    sha = hashlib.sha256(dados).hexdigest()
    validas, data_ref, erros = validar(dados, max_linhas)
    if erros:
        tid = conn.execute("""INSERT INTO op.importacao_tentativa (loja_id, tipo, sha256, usuario_id, n_erros, relatorio)
                              VALUES (%s, 'estoque', %s, %s, %s, %s) RETURNING id""",
                           (loja_id, sha, usuario_id, len(erros), Jsonb(erros))).fetchone()[0]
        conn.commit()
        return {"status": "rejeitada", "tentativa_id": tid, "n_erros": len(erros), "erros": erros[:50]}

    mapa = dict(conn.execute("SELECT codigo, produto_id FROM op.codigo_externo WHERE loja_id = %s", (loja_id,)).fetchall())
    gravar = [(mapa[c], q) for c, q, _ in validas if c in mapa]
    ignoradas = len(validas) - len(gravar)
    with conn.transaction():
        r = conn.execute("""INSERT INTO op.importacao (loja_id, tipo, sha256, data_referencia, linhas_ok, linhas_ignoradas)
                            VALUES (%s, 'estoque', %s, %s, %s, %s) ON CONFLICT (loja_id, tipo, sha256) DO NOTHING RETURNING id""",
                         (loja_id, sha, data_ref, len(gravar), ignoradas)).fetchone()
        if r is None:
            orig = conn.execute("SELECT id FROM op.importacao WHERE loja_id = %s AND tipo = 'estoque' AND sha256 = %s",
                                (loja_id, sha)).fetchone()[0]
            return {"status": "duplicada", "importacao_id": orig}
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO op.snapshot_estoque (importacao_id, produto_id, data_referencia, quantidade)
                               VALUES (%s, %s, %s, %s)""", [(r[0], p, data_ref, q) for p, q in gravar])
    presentes = {p for p, _ in gravar}
    ausentes = sorted(c for c, p in mapa.items() if p not in presentes)
    return {"status": "aceita", "importacao_id": r[0], "data_referencia": data_ref.isoformat(),
            "linhas_gravadas": len(gravar), "linhas_ignoradas": ignoradas, "codigos_ausentes": ausentes[:500]}


def _neutralizar(v) -> str:
    """Impede que planilhas interpretem a célula como fórmula."""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def relatorio_para_csv(erros) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(["linha", "campo", "erro", "valor"])
    for e in erros:
        w.writerow([_neutralizar(e.get(k, "")) for k in ("linha", "campo", "erro", "valor")])
    return out.getvalue()
