# Shelf-to-Profit

Backend do MVP: banco com invariantes, API (sessão, upload, importação, alertas), worker de visão
com reserva curta, regra de alerta r2 e reconciliação de arquivos.

## Estrutura

    app/            código da API, worker, importação, regras e reconciliação
    db/             papéis do PostgreSQL (executar uma vez por servidor)
    migrations/     migrações Alembic (SQL em migrations/sql/)
    tests/          testes de banco (tests/db) e da aplicação (tests/app)
    scripts/        verificação por mutação
    docs/           evidências dos testes
    .github/        pipeline de CI

## Rodar os testes

    pip install -r requirements-dev.txt
    # PostgreSQL 16 acessível com um superusuário descartável:
    export STP_PG_HOST=localhost STP_PG_PORT=5432 STP_PG_USER=postgres STP_PG_PASSWORD=...
    python -m pytest -q tests          # recria o banco "stp" e os papéis stp_* a cada execução
    python scripts/mutacoes.py         # remove cada proteção numa cópia e exige que um teste falhe

Use apenas um PostgreSQL de teste: a suíte apaga e recria o banco `stp` e os papéis `stp_*`.

## Estado do Gate D (27/10/2026)

| Entrega | Implementado | Testado localmente | CI (GitHub) |
|---|---|---|---|
| 1. Schema em migrações Alembic (0001 base, 0002 Gate D) | sim | 62 testes de banco | pendente: repositório ainda não existe |
| 1. Verificação por mutação automatizada | sim | 16 de 16 detectadas | pendente |
| 2. Sessão (argon2id, cookie HttpOnly/SameSite=Strict, revogação) e autorização por papel e loja | sim | 25 testes de API (com upload) | pendente |
| 2. Upload: streaming, limite, assinatura JPEG/PNG, quarentena UUID 0600, idempotência, falha de banco e disco | sim | sim | pendente |
| 2. Importação CSV: três classes de linha, tudo ou nada, relatório neutralizado | sim | 28 testes | pendente |
| 3. Worker: reserva com token, decodificação em subprocesso com limites, conclusão atômica, retomada, descarte de atrasado | sim | 15 testes | pendente |
| 4. Regra r2 (atualidade, margem, inconclusivo, planograma pendente) | sim | 19 casos | pendente |
| 4. Reconciliação (aborto, trava, lixeira, imagem ausente) | sim | 7 testes | pendente |
| Método de visão real (alinhamento ArUco + linha de base B1) | **não** | não | não |
| Planta 2D (front-end) | **não** | não | não |
| Contêineres (worker sem rede, FS somente leitura) | **não** | não | não |

Total local: **156 testes aprovados** (PostgreSQL 16.15, Python 3.13) e 16 mutações detectadas; ver `docs/evidencias/`.
Também executado por TCP com senha, como no CI. O workflow está em `.github/workflows/ci.yml` (actions fixadas por SHA).

## Decisões registradas nesta entrega

- **Sem ORM.** psycopg 3 + SQL. As regras críticas estão no banco; um ORM não removeria código.
- **Upload como corpo bruto** (`Content-Type: image/jpeg`), não multipart: o limite de tamanho vale enquanto os bytes chegam, sem arquivo temporário do framework.
- **CSV canônico** `codigo;ean;descricao;quantidade;data`. Exportações reais serão convertidas por um adaptador quando o formato da loja for conhecido.
- **Quantidade sem separador de milhar** ("1.234" é erro): evita a ambiguidade entre 1,234 e 1234.
- **Código do sistema de gestão só por `op.codigo_externo`**, sem tentar casar por EAN: um mapeamento errado gera alertas errados.

## Problemas encontrados durante a implementação (todos corrigidos e com teste)

| Problema | Impacto | Correção |
|---|---|---|
| `op.concluir_captura` falhava quando já havia alerta aberto do mesmo tipo na posição; após 3 tentativas a captura era recusada | Alto: fotos válidas perdidas a cada ronda repetida | Migração 0002, `ON CONFLICT DO NOTHING`; teste `divergencia_gera_alerta_uma_vez`, mutação A1 |
| `row_factory` ajustado na conexão do pool vazava para a próxima requisição; o id do usuário virava o texto "id" | Alto: identidade errada na autorização (aqui resultou em 503) | `row_factory` só no cursor; coberto por `test_isolamento_entre_lojas` |
| Pillow 12.3.0 com 38 vulnerabilidades conhecidas (corrigidas na 13.0.0) | Alto: é o decodificador de arquivos não confiáveis | Atualizado para 13.0.0; `pip-audit` no CI e semanal |
| Teste de snapshot velho não isolava a janela de atualidade | Médio: a proteção podia sumir sem o teste notar | Teste refeito; mutação M2 detectada |
| Mutação M3 alterava a versão antiga da função (substituída pela 0002) | Médio: falsa sensação de cobertura | Mutações apontam para a versão vigente |
