# Arquitetura

O Shelf-to-Profit compara o que a foto de uma gôndola mostra com o estoque registrado no
sistema de gestão da loja e aponta **possíveis divergências** numa planta 2D. Uma pessoa
confirma ou rejeita cada alerta; o sistema não trata nenhuma detecção como fato.

## Componentes

| Componente | Papel |
|---|---|
| API (FastAPI) | Sessão, upload de fotos, importação de estoque em CSV, alertas e dados da planta |
| Worker | Processa as fotos da fila: valida, re-codifica, alinha pelos marcadores e classifica cada posição |
| PostgreSQL | Fonte da verdade; as regras de integridade ficam no próprio banco |
| Interface (`/ui/`) | Planta 2D em SVG, sem dependências externas |

O worker não fala com a API: a fila é a própria tabela de capturas, reservada com prazo e
token. Fotos e alertas de uma captura são gravados numa única transação.

## Fluxo de uma foto

1. O celular envia a foto para a API, que confere tamanho e formato e grava o arquivo em quarentena.
2. O worker reserva a captura, decodifica a imagem num processo separado com limites de memória
   e tempo e grava uma versão re-codificada, sem metadados.
3. Os quatro marcadores ArUco da gôndola identificam a face e corrigem a perspectiva.
4. Cada posição é comparada com a foto de referência da gôndola abastecida (textura e cor).
5. A regra de alerta cruza a observação com o estoque importado mais recente e válido.

## Estados exibidos na planta

Divergência confirmada, possível divergência, inconclusivo, sem foto recente, vazio coerente
com o sistema, parcial e abastecida. Foto inadequada (tremida, escura, sem marcadores
suficientes) nunca gera alerta: a planta mostra a situação da foto.

## Decisões

- Sem ORM: as consultas e as regras críticas ficam em SQL, testadas diretamente no banco.
- Upload como corpo bruto, para limitar o tamanho enquanto os bytes chegam.
- Importação de CSV tudo ou nada: qualquer linha inválida rejeita o arquivo.
- Histórico só de inserção: correções viram novos registros.

## Limitações conhecidas

- Os limiares da visão foram calibrados com imagens sintéticas; ainda não há validação com fotos reais.
- Fundo texturizado e embalagens parecidas com o fundo reduzem a confiança, e essas posições ficam inconclusivas.
- Cada gôndola precisa de uma foto de referência atualizada quando o planograma muda.
