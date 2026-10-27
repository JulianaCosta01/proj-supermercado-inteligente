"""Verificação por mutação: cada proteção é removida numa cópia do SQL e o teste
correspondente PRECISA falhar. Sai com código 1 se alguma mutação sobreviver.
Uso: python scripts/mutacoes.py   (mesmas variáveis STP_PG_* dos testes)"""
import os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (id, arquivo relativo à raiz ou a migrations/sql, trecho original, substituição, filtro de testes)
MUTACOES = [
    ("M1 sem checagem de NULL na transição", "0001_base.sql",
     "IF p_alerta IS NULL OR p_versao_esperada IS NULL OR p_para IS NULL OR p_usuario IS NULL THEN", "IF false THEN", "f01"),
    ("M2 sem frescor do snapshot", "0001_base.sql",
     "IF v_snap_data > v_dia_captura OR v_snap_data < v_dia_captura - v_max THEN", "IF false THEN", "f03_snapshot_velho or f03_snapshot_do_futuro"),
    ("M3 sem cerca por token (versão vigente da função, migração 0002)", "0002_gate_d.sql",
     "OR c.reserva_token IS DISTINCT FROM p_token THEN", "THEN", "w03"),
    ("M4 função sem search_path", "0001_base.sql",
     "LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$", "LANGUAGE plpgsql AS $$", "f06_toda"),
    ("M5 sem privilégio padrão fechado", "0001_base.sql",
     "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;", "", "f05 or i09"),
    ("M6 TEMPORARY para PUBLIC", "roles.sql",
     "REVOKE TEMPORARY ON DATABASE {db} FROM PUBLIC;", "", "f04_sem"),
    ("M7 sem REVOKE da função de transição (worker confirmaria)", "0001_base.sql",
     "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;", "", "i01_worker_nao_executa"),
    ("M8 sem restrição de exclusão do planograma", "0001_base.sql",
     "  EXCLUDE USING gist (posicao_id WITH =, vigencia WITH &&)\n", "  CHECK (true)\n", "i04_vigencias"),
    ("A1 conclusão sem ON CONFLICT (alerta repetido derruba a captura)", "0002_gate_d.sql",
     "  ON CONFLICT (posicao_id, tipo) WHERE status IN ('aberto', 'em_verificacao') DO NOTHING;", ";", "divergencia_gera_alerta_uma_vez"),
    ("A2 upload sem a verificação final de assinatura", "app/api.py",
     "if total == 0 or not (inicio.startswith(JPEG) or inicio.startswith(PNG)):", "if total == 0:", "upload_recusa_formato"),
    ("A3 regra sem margem", "app/regras.py", "if disponivel < f.margem:", "if False:", "test_regra"),
    ("A4 reconciliação sem trava", "app/reconciliacao.py",
     "if candidatos > 200 or (candidatos > 10 and candidatos > 0.05 * max(total, 1)):", "if False:", "trava"),
    ("A5 reconciliação ignora falha do banco", "app/reconciliacao.py",
     "        res.abortada = f\"banco indisponível: {type(e).__name__}\"      # (2)\n        log.error(\"reconciliação abortada: %s\", res.abortada)\n        return res",
     "        capturas, n_capturas = [], 1", "banco_fora_do_ar"),
    ("A6 importação grava linhas válidas mesmo com erro", "app/importacao.py", "    if erros:\n        tid", "    if False:\n        tid", "arquivo_invalido_nao_grava_nada"),
    ("A7 desativar usuário sem derrubar sessões (gatilho)", "0002_gate_d.sql",
     "    DELETE FROM op.sessao WHERE usuario_id = NEW.id;", "    NULL;", "desativar_usuario"),
    ("A8 worker apaga quarentena antes do commit", "app/worker.py",
     "        try:\n            captura, posicoes = self._contexto(captura_id)", "        quarentena.unlink()\n        try:\n            captura, posicoes = self._contexto(captura_id)", "queda_no_meio"),
    ("V1 sem bloqueio de foto desfocada", "app/visao.py",
     "        if nit < max(NITIDEZ_ABSOLUTA_MIN, NITIDEZ_RELATIVA_MIN * nit_ref):", "        if False:", "imagem_ruim_vira_inconclusivo"),
    ("V2 foto inadequada ainda abre alertas", "app/worker.py",
     'alertas = [] if (alin is not None and alin.situacao != "alinhada") else', "alertas =", "tremida_registra_situacao"),
    ("V3 marcador repetido entre faces aceito", "0003_visao.sql", "  UNIQUE (loja_id, marcador_id)\n", "  CHECK (true)\n", "marcador_repetido"),
    ("V4 vazio só pela textura (sem a pista de cor)", "app/visao.py",
     "    if vazio_c and (vazio_t or r < LIMIAR_OCUPADO):", "    if vazio_t or vazio_c:", "perfurado or classifica_posicoes"),
    ("L1 login sem bloqueio ao passar do limite", "0004_limite_login.sql",
     "    ELSIF v_falhas > k.limite THEN", "    ELSIF false THEN", "limite_login"),
    ("L2 login sem normalizar maiúsculas", "0004_limite_login.sql",
     "  v_login text := lower(left(coalesce(p_login, ''), 60));\n  v_ip text := left(coalesce(p_ip, 'desconhecido'), 45);\n  k record;",
     "  v_login text := left(coalesce(p_login, ''), 60);\n  v_ip text := left(coalesce(p_ip, 'desconhecido'), 45);\n  k record;", "maiusculas"),
    ("L3 login verifica a senha mesmo bloqueado", "app/api.py", "            if espera > 0:", "            if False:", "senha_certa_passa"),
    ("V5 referência sem verificação de hash", "app/visao.py",
     "        if hashlib.sha256(dados).hexdigest() != sha:", "        if False:", "adulterada"),
]


def main():
    sobreviventes = []
    for nome, arq, de, para, filtro in MUTACOES:
        with tempfile.TemporaryDirectory() as d:
            copia = pathlib.Path(d) / "repo"
            shutil.copytree(ROOT, copia, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            if arq == "roles.sql":
                alvo = copia / "db" / "roles.sql"
            elif arq.endswith(".sql"):
                alvo = copia / "migrations" / "sql" / arq
            else:
                alvo = copia / arq
            texto = alvo.read_text()
            assert texto.count(de) >= 1, f"trecho da mutação '{nome}' não encontrado"
            alvo.write_text(texto.replace(de, para, 1))
            env = {k: v for k, v in os.environ.items() if k not in ("STP_SQL_DIR", "STP_ROLES_FILE")}
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests", "-k", filtro, "-p", "no:cacheprovider"],
                               cwd=copia, env=env, capture_output=True, text=True)
            ultima = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
            morta = r.returncode == 1 and "failed" in ultima
            print(f"{'detectada ' if morta else 'SOBREVIVEU'}  {nome}: {ultima}")
            if not morta:
                sobreviventes.append(nome)
    sys.exit(1 if sobreviventes else 0)


if __name__ == "__main__":
    main()
