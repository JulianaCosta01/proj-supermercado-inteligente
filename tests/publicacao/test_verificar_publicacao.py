"""Verificação de publicação contra repositórios temporários com problemas plantados."""
import io
import pathlib
import subprocess
import sys

import pytest
from PIL import Image

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "verificar_publicacao.py"
NOREPLY = "1+teste@users.noreply.github.com"
# Valores montados em partes para que este arquivo não contenha o padrão literal.
CHAVE_PRIVADA = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n"
TOKEN_GH = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SENHA_X = "Sup3r" + "S3creta"
EMAIL_X = "fulano.silva" + "@" + "gmail.com"
CPF_X = "123.456" + ".789-09"


TIPOS_IDS = {"chave privada", "token do GitHub", "URL de conexão com senha", "valor de credencial", "variável de credencial",
             "senha em SQL", "e-mail", "caminho pessoal de máquina", "CPF", "link de sessão"}


def git(repo, *args, email=NOREPLY, msg_env=None):
    env = {"GIT_AUTHOR_NAME": "Teste", "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": "Teste",
           "GIT_COMMITTER_EMAIL": email, "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("projeto\n")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "docs: inicia o repositório de teste")
    return tmp_path


def commit(repo, arquivos: dict, msg="feat: adiciona arquivo de teste", email=NOREPLY):
    for nome, conteudo in arquivos.items():
        p = repo / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        if conteudo is None:
            p.unlink()
        elif isinstance(conteudo, bytes):
            p.write_bytes(conteudo)
        else:
            p.write_text(conteudo)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", msg, email=email)


def verificar(repo, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *(args or ("--historico", "main"))], cwd=repo, capture_output=True, text=True)
    return r.returncode, r.stdout


def png(gps=False):
    im, buf = Image.new("RGB", (8, 8)), io.BytesIO()
    exif = Image.Exif()
    if gps:
        exif[0x8825] = {1: "S", 2: (2.0, 31.0, 0.0)}
    im.save(buf, "PNG", exif=exif)
    return buf.getvalue()


def test_repositorio_limpo_passa(repo):
    commit(repo, {"app.py": "x = 1\n", "conf.yml": "STP_PG_PASSWORD: ${SENHA}\nPOSTGRES_PASSWORD_FILE: /run/secrets/pg\n"})
    assert verificar(repo)[0] == 0


@pytest.mark.parametrize("nome,conteudo,tipo", [
    ("deploy/chave.txt", CHAVE_PRIVADA, "chave privada"),
    ("app/config.py", f'TOKEN = "{TOKEN_GH}"\n', "token do GitHub"),
    ("app/db.py", f'URL = "postgresql://app:{SENHA_X}@db/stp"\n', "URL de conexão com senha"),
    ("app/cfg.py", f'password = "{SENHA_X}"\n', "valor de credencial"),
    (".github/workflows/x.yml", f"  POSTGRES_PASSWORD: {SENHA_X}\n", "variável de credencial"),
    ("db/seed.sql", f"CREATE ROLE a LOGIN PASSWORD '{SENHA_X}';\n", "senha em SQL"),
    ("docs/notas.md", f"contato: {EMAIL_X}\n", "e-mail"),
    ("docs/notas2.md", "arquivo em C:" + "\\Users\\fulano\\Desktop\\x\n", "caminho pessoal de máquina"),
    ("dados/clientes.md", f"CPF {CPF_X}\n", "CPF"),
    ("docs/log.md", "veja claude.ai/code/" + "session_ABC123def\n", "link de sessão"),
], ids=lambda v: v if v in TIPOS_IDS else "")   # o id do teste não pode conter o valor plantado
def test_detecta_segredos_e_dados_pessoais(repo, nome, conteudo, tipo):
    commit(repo, {nome: conteudo})
    codigo, saida = verificar(repo)
    assert codigo == 1 and tipo in saida, saida
    assert SENHA_X not in saida and TOKEN_GH not in saida and CPF_X not in saida and EMAIL_X not in saida, "o relatório não pode repetir o valor"


@pytest.mark.parametrize("nome,tipo", [
    (".env", "arquivo .env"), ("segredos/pg.txt", "pasta de segredos"), ("pgpass_worker", "arquivo de senha"),
    ("backup/stp.dump", "dump, backup"), ("relatorio.pbix", "planilha ou relatório"), ("docs/auditoria.docx", "documento Word"),
    ("deploy/servidor.pem", "chave ou cofre"),
])
def test_bloqueia_arquivos_proibidos(repo, nome, tipo):
    commit(repo, {nome: "conteudo\n"})
    codigo, saida = verificar(repo)
    assert codigo == 1 and tipo in saida


def test_segredo_apagado_continua_no_historico(repo):
    commit(repo, {"app/db.py": f'URL = "postgresql://app:{SENHA_X}@db/stp"\n'})
    commit(repo, {"app/db.py": 'URL = os.environ["STP_DSN"]\n'}, msg="fix: lê a conexão do ambiente")
    assert verificar(repo, "--historico", "main")[0] == 1
    assert verificar(repo, "--historico", "main~1..main")[0] == 0     # só o último commit: limpo


def test_binario_precisa_de_aprovacao_por_versao(repo):
    imagem = png()
    commit(repo, {"docs/tela.png": imagem})
    codigo, saida = verificar(repo)
    assert codigo == 1 and "binário sem aprovação" in saida
    import hashlib
    (repo / ".publicacao-permitidos").write_text(f"binario {hashlib.sha256(imagem).hexdigest()} docs/tela.png\n")
    assert verificar(repo)[0] == 0
    commit(repo, {"docs/tela.png": png() + b"\x00"}, msg="docs: troca a captura de tela")
    assert verificar(repo)[0] == 1                       # nova versão exige nova revisão


def test_imagem_com_gps(repo):
    imagem = png(gps=True)
    import hashlib
    (repo / ".publicacao-permitidos").write_text(f"binario {hashlib.sha256(imagem).hexdigest()} foto.png\n")
    commit(repo, {"foto.png": imagem})
    codigo, saida = verificar(repo)
    assert codigo == 1 and "EXIF GPS" in saida


def test_valor_ficticio_aprovado_passa(repo):
    (repo / ".publicacao-permitidos").write_text("ficticio senha-de-teste-123\n")
    commit(repo, {"tests/conftest.py": 'SENHA = "senha-de-teste-123"\n'})
    assert verificar(repo)[0] == 0
    commit(repo, {"tests/outro.py": 'senha = "' + "outra-senha-" + 'real"\n'})
    assert verificar(repo)[0] == 1


def test_metadados_dos_commits(repo):
    commit(repo, {"a.txt": "a\n"}, email=EMAIL_X)
    codigo, saida = verificar(repo)
    assert codigo == 1 and "e-mail do autor não é noreply" in saida and "fulano" not in saida


def test_trailer_de_coautoria_na_mensagem(repo):
    commit(repo, {"b.txt": "b\n"}, msg="feat: adiciona b\n\nCo-Authored" + "-By: Alguem <a@example.com>")
    codigo, saida = verificar(repo)
    assert codigo == 1 and "trailer de coautoria" in saida


def test_modo_indice_olha_so_o_que_vai_ser_commitado(repo):
    (repo / "novo.py").write_text(f'password = "{SENHA_X}"\n')
    git(repo, "add", "novo.py")
    codigo, saida = verificar(repo, "--indice")
    assert codigo == 1 and "novo.py" in saida
