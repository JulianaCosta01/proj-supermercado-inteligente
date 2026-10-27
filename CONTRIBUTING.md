# Como contribuir

## Comentários

Comente o motivo, não o óbvio. Um comentário vale quando explica uma regra de negócio, uma
restrição de segurança, um caso extremo ou um comportamento inesperado que o código sozinho não deixa claro.

- Sem separadores visuais (`# ----`, `-- ====`) e sem comentários que anunciem blocos, fases ou etapas.
- Sem comentários que repetem o nome da função ou descrevem cada linha.
- Sem TODO/FIXME genérico: só com o problema concreto descrito.
- Preserve comentários que explicam invariantes, limitações reais e riscos de regressão.
- Remova comentários desatualizados ou que afirmem algo que o código não garante.
- Português ou inglês, seguindo o idioma já usado no arquivo.

```python
# Rejeita o arquivo inteiro para evitar importar um estoque parcial.
def validar_arquivo(arquivo):
    ...
```

## Commits

Formato [Conventional Commits](https://www.conventionalcommits.org/): `tipo: descrição curta no presente`.

Tipos usados: `feat`, `fix`, `test`, `refactor`, `security`, `build`, `ci`, `docs`, `style`, `chore`, `perf`.

```
fix: impede duplicação de observações após retomada do worker
security: restringe o acesso às capturas por loja
```

- Um commit, uma mudança coerente. Funcionalidades independentes vão em commits separados.
- Nada de mensagens vagas (`updates`, `fixes`, `final version`), relatórios de atividade, emojis
  ou referências a conteúdo gerado por IA.
- Nunca versionar credenciais, `.env`, a pasta `segredos/` ou dados reais de loja.
- Não reescrever o histórico publicado.

O hook em `.githooks/commit-msg` verifica o formato. Para ativar no seu clone:

    git config core.hooksPath .githooks
