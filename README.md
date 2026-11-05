# PLATAFORMA DE INTELIGÊNCIA VISUAL E MODELAGEM ESPACIAL PARA VAREJO 

<div align="center">

**VISÃO COMPUTACIONAL · MONITORAMENTO DE GÔNDOLAS · IA APLICADA À MODELAGEM DE SISTEMAS COMPLEXOS**

Identificação de possíveis divergências entre imagens de gôndolas e o estoque registrado, com visualização dos resultados em uma planta 2D, que posteriormente será evoluída para um ecossistema 3D.

<br/>

![Python](https://img.shields.io/badge/Python-29263F?style=for-the-badge&logo=python&logoColor=C6A8E8)
![OpenCV](https://img.shields.io/badge/OpenCV-29263F?style=for-the-badge&logo=opencv&logoColor=D8C59A)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-29263F?style=for-the-badge&logo=postgresql&logoColor=C6A8E8)
![Docker](https://img.shields.io/badge/Docker-29263F?style=for-the-badge&logo=docker&logoColor=C6A8E8)

</div>

---

## 01. Visão geral

O projeto está em desenvolvimento e analisa imagens de gôndolas e compara as posições dos produtos com uma referência visual cadastrada. As possíveis divergências são organizadas em uma **planta 2D**, permitindo acompanhar posições, imagens e ocorrências.

**Um possível empecilho não é uma ruptura confirmada.** O sistema analisa as imagens e pode recomendar uma verificação, mas a confirmação permanece sob responsabilidade humana.

## 02. Como funciona

1. **Identificação:** quatro marcadores ArUco identificam cada face da gôndola.
2. **Alinhamento:** a perspectiva é corrigida por homografia.
3. **Validação:** resolução, exposição, nitidez e alinhamento são verificados.
4. **Comparação:** cada posição é analisada por duas pistas: textura (bordas) e cor (matiz e saturação).
5. **Classificação:** a confiança determina se existe evidência suficiente para sugerir uma verificação.
6. **Visualização:** os resultados aparecem na planta 2D para análise humana.

### Regras da análise

- Sem marcadores: captura recusada.
- Imagem inadequada: posições indeterminadas, sem alertas.
- Confiança abaixo de `0,80`: no máximo, alerta de verificação (V).
- Confiança alta: exige concordância entre as duas pistas visuais.

<sub>Método de visão computacional: B1-referencia 1.0.0</sub>

## 03. Arquitetura

```text
app/
├── API, worker e visão computacional (B1)
├── regras de alerta e reconciliação
├── importação
└── planta 2D

app/ui/       Interface HTML + JavaScript
db/           PostgreSQL e configuração de acesso
migrations/   Migrações Alembic e SQL
tests/        Banco, API, visão, planta e isolamento
scripts/      Avaliação, validação e mutações
docs/         Arquitetura, segurança e evidências

Dockerfile
compose.yaml
```

A interface é servida em `/ui/` e não depende de frameworks JavaScript externos.

**Documentação técnica:**

- [Arquitetura](docs/arquitetura.md)
- [Segurança](docs/seguranca.md)
- [Contribuição e publicação](CONTRIBUTING.md)

## 04. Testes e resultados

<sub>POSIÇÃO EM 27/10/2026</sub>

| Verificação | Resultado local |
|---|---:|
| Visão computacional B1 | 26 testes sintéticos + 6 de ponta a ponta |
| Planta 2D | 18 testes, incluindo Chromium |
| Isolamento do worker | 2 testes em Docker |
| Limite de tentativas de login | 10 testes |
| Verificação de publicação | 25 testes |
| Mutações detectadas | **27/27** |
| `pip-audit` | Sem vulnerabilidades conhecidas |

<badge color="success">241 testes + 2 de isolamento aprovados localmente</badge>

**Ainda pendente:**

- Validação com fotografias reais.
- Construção e teste da imagem Docker a partir do Dockerfile.
- Execução da CI no GitHub.

As evidências estão disponíveis em `docs/evidencias/`.

## 05. Limitações e próximos passos

A avaliação atual utiliza imagens sintéticas. Portanto, os resultados ainda não representam métricas de precisão em ambiente comercial.

Limitações identificadas:

- Fundos texturizados podem reduzir a confiança na identificação de posições vazias.
- Embalagens visualmente semelhantes ao fundo podem ser classificadas como parciais ou indeterminadas.
- Produtos trocados podem não apresentar diferenças visuais suficientes.
- Os limiares de análise precisam ser recalibrados com fotografias reais.
- Alterações de embalagem ou planograma exigem uma nova referência da face.

**Próxima etapa:** validar o método em uma gôndola real ou montada pela equipe, com 30 fotografias distribuídas em três horários, incluindo posições vazias e anotações humanas.

### Preparar os marcadores e validar as fotografias

```bash
python scripts/gerar_marcadores.py --ids 0 1 2 3 --tamanho-mm 60 --saida marcadores.pdf

python scripts/validar_fotos_reais.py pasta_da_validacao/
```

## 06. Executar os testes
### Instalar dependências
```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
```

### Configurar o PostgreSQL

A suíte utiliza PostgreSQL 16 descartável. **Ela apaga e recria o banco `stp` e os papéis `stp_*`. Não execute em um banco com dados importantes.**

```bash
export STP_PG_HOST=localhost
export STP_PG_PORT=5432
export STP_PG_USER=postgres
export STP_PG_PASSWORD=...
export STP_PG_DESCARTAVEL=1
```

### Executar a validação

```bash
python -m pytest -q tests
python scripts/mutacoes.py
python scripts/avaliar_sintetico.py 60
```

Teste de isolamento, que requer Docker:

```bash
sudo -E python -m pytest -q tests/isolamento -m isolamento
```

## 07. Segurança e implantação

A arquitetura aplica isolamento entre API, banco e worker:

- PostgreSQL sem TCP, com comunicação por socket Unix e autenticação SCRAM.
- Worker sem rede, com raiz somente leitura, `cap_drop: ALL` e `no-new-privileges`.
- Limites de memória e processos.
- Acesso restrito às áreas de dados necessárias.
- API publicada em `127.0.0.1:8000`, atrás do Nginx.
- Segredos separados para API e worker, com permissões restritas.

Detalhes e justificativas: [docs/seguranca.md](docs/seguranca.md).

---

<div align="center">

**SUPERMERCADO INTELIGENTE**

<sub>Visão computacional · Inteligência de estoque · Engenharia de software</sub>

</div>
