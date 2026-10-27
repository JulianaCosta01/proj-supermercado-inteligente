# Shelf-to-Profit

Detecta **possíveis divergências** entre o que a foto da gôndola mostra e o estoque registrado,
e mostra o resultado numa planta 2D. Uma pessoa confirma; o sistema nunca chama de ruptura algo não confirmado.

## Estrutura

    app/            API, worker, visão (B1), regra de alerta, importação, reconciliação, planta
    app/ui/         planta 2D (HTML + JS sem dependências, servida em /ui/)
    db/             papéis do PostgreSQL e pg_hba do piloto
    migrations/     migrações Alembic (SQL em migrations/sql/)
    tests/          db/ (invariantes), app/ (API, worker, planta), visao/ (imagens sintéticas),
                    isolamento/ (worker em contêiner), sintetico.py (gerador de fotos sintéticas)
    scripts/        mutações, avaliação sintética, validação com fotos reais, folha de marcadores
    docs/           evidências dos testes (docs/evidencias/)
    Dockerfile, compose.yaml

Padrão de comentários e commits: [CONTRIBUTING.md](CONTRIBUTING.md).

## Como a visão funciona (método B1-referencia 1.0.0)

1. Cada face de gôndola tem 4 marcadores ArUco impressos (ids únicos na loja) e a geometria das posições em mm.
2. Uma foto da face abastecida vira a **referência** (`python -m app.cadastro referencia ...`).
3. Em cada foto: detecta os marcadores, identifica a face, corrige a perspectiva (homografia) e
   verifica erro de alinhamento, resolução, exposição e nitidez.
4. Para cada posição compara com a referência duas pistas: **textura** (bordas) e **cor por faixas** (matiz/saturação).
   Só há confiança alta quando as duas concordam.
5. Foto sem marcadores → captura recusada. Foto ruim → todas as posições "indeterminado", **sem alertas**,
   e a planta mostra a situação da foto. Confiança abaixo de 0,80 → no máximo alerta "verificar" (V).

## Estado (27/10/2026)

| Item | Implementado | Testado localmente | CI (GitHub) |
|---|---|---|---|
| Visão B1: marcadores, perspectiva, falhas de imagem, 2 pistas | sim | 26 testes sintéticos + 6 de ponta a ponta | não executado |
| Planta 2D integrada (estados, painel, ações, foto) | sim | 18 testes, incluindo Chromium real | não executado |
| Worker isolado (sem rede, raiz só leitura, sem capabilities, uid 10001) | sim | 2 testes em contêiner Docker | não executado |
| Banco, API, importação, worker, regra, reconciliação (entregas anteriores) | sim | sim | não executado |
| **Validação com fotos reais** | roteiro pronto | **não feita: não há fotos reais** | — |
| Imagem Docker construída a partir do Dockerfile | Dockerfile pronto | **não**: o ambiente de desenvolvimento não acessa registros de imagens | job pronto |

Totais locais: **206 testes** + 2 de isolamento aprovados; **21 de 21 mutações detectadas**; `pip-audit` sem vulnerabilidades conhecidas.
Evidências em `docs/evidencias/` (execuções, mutações, avaliação sintética, isolamento, capturas de tela da planta).

## Limitações conhecidas (objetivas)

- **Nenhuma métrica de loja existe.** Os números de `avaliacao_sintetica.txt` vêm de fotos geradas; servem para regressão, não para afirmar precisão.
- **Fundo texturizado (painel perfurado):** nas cenas sintéticas, todos os vazios reais ficaram com confiança baixa (viram V ou nada). O método erra para o lado seguro, mas perde as divergências.
- **Embalagens parecidas com o fundo** (cinza, branco, sem estampa) e **produto trocado** tendem a "parcial"/confiança baixa.
- **Limiares calibrados só em imagens sintéticas** (nitidez, exposição, textura, cor). Precisam ser recalibrados com fotos reais.
- **Referência por face:** mudança de embalagem ou de planograma exige nova foto de referência.
- O teste de isolamento local usa um contêiner com o Python do host montado só para leitura; ele valida os controles (rede, raiz, capabilities, banco), não a imagem do Dockerfile. O job de CI faz o teste com a imagem construída.

## Próximo passo para uma demonstração confiável

Fotografar uma gôndola real (ou montada pela equipe) com os marcadores impressos e rodar:

    python scripts/gerar_marcadores.py --ids 0 1 2 3 --tamanho-mm 60 --saida marcadores.pdf   # imprimir em 100%
    python scripts/validar_fotos_reais.py pasta_da_validacao/   # face.json, referencia.jpg, fotos/, anotacoes.csv

Sugestão mínima: 30 fotos em 3 horários, com 20% das posições vazias, anotadas por uma pessoa.

## Rodar os testes

    pip install -r requirements-dev.txt && python -m playwright install chromium
    export STP_PG_HOST=localhost STP_PG_PORT=5432 STP_PG_USER=postgres STP_PG_PASSWORD=...   # PostgreSQL 16 descartável
    python -m pytest -q tests                                   # recria o banco "stp" e os papéis stp_*
    python scripts/mutacoes.py                                  # cada proteção removida precisa ser detectada
    sudo -E python -m pytest -q tests/isolamento -m isolamento  # precisa de Docker
    python scripts/avaliar_sintetico.py 60

## Implantação do piloto (resumo)

- `compose.yaml`: banco sem TCP; API e worker falam com ele **só por socket Unix**, com senha (scram), conforme `db/pg_hba.conf`.
  Sem esse pg_hba, o padrão da imagem oficial (trust no socket) deixaria qualquer contêiner com o socket entrar como superusuário.
- Arquivos `segredos/pgpass_app` e `segredos/pgpass_worker` no formato .pgpass, dono uid 10001, permissão 600.
- Worker: `network_mode: none`, raiz somente leitura, `cap_drop: ALL`, `no-new-privileges`, limite de memória e de processos;
  grava só em `quarentena` e `imagens`; `referencias` somente leitura.
- API: publicada só em 127.0.0.1:8000, atrás do nginx (TLS e limites).

## Decisões registradas

- Sem ORM (psycopg 3 + SQL); upload como corpo bruto; CSV canônico `codigo;ean;descricao;quantidade;data`.
- Temporários e lixeira **dentro de cada área de dados**: renomear nunca cruza volumes, e cada volume é montado só onde é preciso.
- Foto inadequada não gera alertas: a planta mostra a situação da foto em vez de encher a fila de "verificar".
