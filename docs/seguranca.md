# Segurança

Este projeto é um protótipo acadêmico. Os controles abaixo estão implementados e testados,
mas não substituem uma revisão própria antes de qualquer uso com dados reais.

## Como reportar uma vulnerabilidade

Use o recurso de relato privado de vulnerabilidades do GitHub neste repositório
(aba *Security*). Não abra issue pública com detalhes de exploração.

## Controles implementados

- **Autorização por loja e papel:** a loja vem sempre da sessão, nunca do corpo da requisição.
  Um teste percorre todas as rotas e falha se alguma nova ficar sem sessão.
- **Sessão:** cookie `HttpOnly`, `Secure` e `SameSite=Strict`, token guardado só como hash, revogação
  imediata ao sair ou ao desativar o usuário. Senhas com argon2id.
- **Limite de tentativas de login:** por conta e IP, por conta e por IP, com bloqueio temporário.
  A resposta é a mesma para conta existente ou não, e a senha não é verificada durante o bloqueio.
- **Arquivos enviados:** limite de tamanho, só JPEG ou PNG, quarentena com nome aleatório, decodificação
  isolada com limites de recursos e re-codificação sem metadados (inclusive localização).
- **Importação de CSV:** validação estrita, gravação atômica e relatório de erros protegido contra fórmulas.
- **Banco de dados:** papéis com privilégio mínimo; o worker não confirma alertas nem escreve direto em
  tabelas; histórico só de inserção; funções privilegiadas com `search_path` fixo.
- **Isolamento do worker:** contêiner sem rede, sistema de arquivos somente leitura, sem capabilities,
  usuário sem privilégios; o banco só aceita conexões locais autenticadas por senha.

## Invariantes testadas no banco

| Código | Invariante |
|---|---|
| I-01 | Uma previsão do modelo nunca vira confirmação humana |
| I-02 | Todo alerta aponta para a observação e o estoque que o originaram |
| I-03 | O mesmo arquivo ou a mesma foto não geram registros duplicados |
| I-04 | Mudar o planograma não reescreve observações antigas |
| I-05 | Decisões concorrentes não se sobrescrevem |
| I-06 | O histórico não é alterado nem apagado |
| I-07 | A leitura analítica só vê dados reais e não escreve |
| I-08 | A exclusão de uma loja segue um procedimento controlado |
| I-09 | Nenhuma função privilegiada fica aberta a todos os papéis |

`scripts/mutacoes.py` remove cada proteção numa cópia do código e exige que algum teste falhe.

## Publicação

`scripts/verificar_publicacao.py` roda nos hooks `pre-commit` e `pre-push` e no CI. Ele recusa
segredos, dados pessoais, arquivos internos e binários não revisados em qualquer commit do histórico.
