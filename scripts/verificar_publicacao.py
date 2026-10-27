"""Verifica se o que vai ser publicado contém segredos, dados pessoais ou arquivos internos.

Modos:
  --historico [REVS...]  todos os arquivos de todos os commits (padrão: main) e os metadados dos commits
  --indice               arquivos preparados para o próximo commit (usado no pre-commit)

Sai com código 1 se houver qualquer achado. Os valores encontrados nunca são impressos,
só o arquivo, a linha e o tipo do problema: o relatório pode ser lido sem vazar nada.

Binários só passam se estiverem em .publicacao-permitidos com o SHA-256 conferido:
toda captura de tela ou imagem nova precisa de revisão explícita.
"""
import argparse
import hashlib
import io
import pathlib
import re
import subprocess
import sys
import zipfile

RAIZ = pathlib.Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                                    check=True).stdout.strip())
LIMITE_BYTES = 2 * 1024 * 1024

CAMINHOS_PROIBIDOS = [
    (re.compile(r"(^|/)\.env(\.[^/]*)?$"), "arquivo .env"),
    (re.compile(r"(^|/)segredos/"), "pasta de segredos"),
    (re.compile(r"(^|/)(\.pgpass|pgpass[^/]*)$"), "arquivo de senha do PostgreSQL"),
    (re.compile(r"\.(pem|key|p12|pfx|jks|kdbx)$", re.I), "chave ou cofre de credenciais"),
    (re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$"), "chave SSH"),
    (re.compile(r"\.(dump|backup|bak|sql\.gz|tar\.gz|bundle)$", re.I), "dump, backup ou pacote"),
    (re.compile(r"\.(pbix|xlsx?|csv)$", re.I), "planilha ou relatório de dados"),
    (re.compile(r"\.docx?$", re.I), "documento Word (documentos internos ficam fora do repositório)"),
]

PADROES_SEGREDO = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "chave privada"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "chave de acesso AWS"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"), "token do GitHub"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"), "token do Slack"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "chave de API do Google"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{32,}"), "chave de API"),
    (re.compile(r"\b(postgres(ql)?|mysql|mongodb|redis)(\+\w+)?://[^:/\s{]+:([^@\s{}]+)@", re.I), "URL de conexão com senha"),
    (re.compile(r"(?i)\b(password|passwd|senha|secret|token|api_?key)\b[\"']?\s*[:=]\s*[\"']([^\"'\s{}$%]{8,})[\"']"), "valor de credencial"),
    (re.compile(r"(?i)\bPASSWORD\s+'([^']{6,})'"), "senha em SQL"),
    (re.compile(r"^\s*-?\s*[A-Z_]*(PASSWORD|SECRET|TOKEN)[A-Z_]*\s*[:=]\s*[\"']?([^\s\"'#]{6,})"), "variável de credencial"),
    (re.compile(r"claude\.ai/code/session_\w+"), "link de sessão"),
    (re.compile(r"(?i)co-authored-by:"), "trailer de coautoria"),
    (re.compile(r"(/home/[a-z][\w.-]+/|/Users/[\w.-]+/|[A-Za-z]:\\\\?Users\\\\?[\w.-]+)"), "caminho pessoal de máquina"),
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "CPF"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "e-mail"),
]
# Domínios de e-mail aceitáveis em texto público.
EMAILS_PERMITIDOS = re.compile(r"@(users\.noreply\.github\.com|example\.(com|org)|exemplo\.com)$", re.I)


def ler_permitidos():
    """Arquivo .publicacao-permitidos: 'binario <sha256> <caminho>' e 'ficticio <valor>'."""
    binarios, ficticios = {}, set()   # binarios: caminho -> conjunto de SHA-256 aprovados
    arq = RAIZ / ".publicacao-permitidos"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            partes = linha.split(maxsplit=2)
            if not partes or linha.startswith("#"):
                continue
            if partes[0] == "binario" and len(partes) == 3:
                binarios.setdefault(partes[2], set()).add(partes[1])
            elif partes[0] == "ficticio" and len(partes) >= 2:
                ficticios.add(linha.split(maxsplit=1)[1])
    return binarios, ficticios


def git(*args, entrada=None):
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, check=True, input=entrada).stdout


def eh_binario(dados: bytes) -> bool:
    return b"\x00" in dados[:8000]


def gps_em_imagem(dados: bytes) -> bool:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(dados)) as im:
            return bool(im.getexif().get_ifd(0x8825)) or b"GPS" in str(im.info).encode()
    except Exception:
        return False


def autor_em_docx(dados: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
        m = re.search(r"<dc:creator>([^<]*)</dc:creator>", core)
        return bool(m and m.group(1).strip() not in ("", "Un-named"))
    except Exception:
        return False


def _marcador(valor: str) -> bool:
    """Exemplos e variáveis não são segredos: '...', '<senha>', '{SENHA}', '$VAR', caminhos de arquivo."""
    v = valor.strip("\"'")
    return (set(v) <= set(".*x") or v.startswith(("<", "{", "$", "/", "%(")) or "{" in v)


def verificar_conteudo(caminho: str, dados: bytes, binarios_ok, ficticios, achados, origem):
    for rx, tipo in CAMINHOS_PROIBIDOS:
        if rx.search(caminho):
            achados.add((origem, caminho, 0, tipo))
    if len(dados) > LIMITE_BYTES:
        achados.add((origem, caminho, 0, f"arquivo maior que {LIMITE_BYTES // 1024 // 1024} MB"))
    if eh_binario(dados):
        if hashlib.sha256(dados).hexdigest() not in binarios_ok.get(caminho, set()):
            achados.add((origem, caminho, 0, "binário sem aprovação em .publicacao-permitidos"))
        if gps_em_imagem(dados):
            achados.add((origem, caminho, 0, "imagem com localização (EXIF GPS)"))
        if caminho.lower().endswith(".docx") and autor_em_docx(dados):
            achados.add((origem, caminho, 0, "documento com autor nos metadados"))
        return
    texto = dados.decode("utf-8", "replace")
    if caminho in (".publicacao-permitidos", "scripts/verificar_publicacao.py", ".githooks/commit-msg"):
        return                                  # contêm os próprios padrões e a lista de fictícios
    for n, linha in enumerate(texto.splitlines(), start=1):
        for rx, tipo in PADROES_SEGREDO:
            for m in rx.finditer(linha):
                valor = m.group(m.lastindex) if m.lastindex else m.group(0)
                if valor in ficticios or m.group(0) in ficticios or _marcador(valor):
                    continue
                if tipo == "e-mail" and EMAILS_PERMITIDOS.search(m.group(0)):
                    continue
                achados.add((origem, caminho, n, tipo))


def verificar_metadados(revs, achados):
    for linha in git("log", "--format=%H%x1f%ae%x1f%ce%x1f%B%x1e", *revs).decode("utf-8", "replace").split("\x1e"):
        if not linha.strip():
            continue
        h, ae, ce, msg = linha.strip("\n").split("\x1f", 3)
        for email, papel in ((ae, "autor"), (ce, "committer")):
            if not EMAILS_PERMITIDOS.search("@" + email.split("@")[-1]):
                achados.add((h[:7], "(metadados)", 0, f"e-mail do {papel} não é noreply"))
        for rx, tipo in PADROES_SEGREDO:
            if tipo in ("link de sessão", "trailer de coautoria", "valor de credencial", "chave privada") and rx.search(msg):
                achados.add((h[:7], "(mensagem)", 0, tipo))


def modo_historico(revs, binarios_ok, ficticios, achados):
    vistos = set()
    commits = git("rev-list", *revs).decode().split()
    for c in commits:
        for linha in git("ls-tree", "-r", c).decode("utf-8", "replace").splitlines():
            meta, caminho = linha.split("\t", 1)
            _, tipo, blob = meta.split()
            if tipo != "blob" or (blob, caminho) in vistos:
                continue
            vistos.add((blob, caminho))
            verificar_conteudo(caminho, git("cat-file", "blob", blob), binarios_ok, ficticios, achados, c[:7])
    verificar_metadados(revs, achados)
    return len(commits)


def modo_indice(binarios_ok, ficticios, achados):
    nomes = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    for caminho in filter(None, nomes):
        verificar_conteudo(caminho, git("show", f":{caminho}"), binarios_ok, ficticios, achados, "índice")


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--historico", nargs="*", metavar="REV")
    g.add_argument("--indice", action="store_true")
    a = ap.parse_args(argv)
    binarios_ok, ficticios = ler_permitidos()
    achados = set()
    if a.indice:
        modo_indice(binarios_ok, ficticios, achados)
        escopo = "índice"
    else:
        n = modo_historico(a.historico or ["main"], binarios_ok, ficticios, achados)
        escopo = f"{n} commits"
    for origem, caminho, linha, tipo in sorted(achados, key=lambda x: (x[1], x[2], x[0])):
        print(f"[{origem}] {caminho}{':' + str(linha) if linha else ''}: {tipo}")
    print(f"verificação de publicação ({escopo}): {'APROVADA' if not achados else f'{len(achados)} achado(s)'}")
    return 1 if achados else 0


if __name__ == "__main__":
    sys.exit(main())
