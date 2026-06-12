---
name: busca-produtos
description: Busca por imagem (Google Lens / reverse image search) para descobrir ONDE COMPRAR um produto. Use quando o usuário fornecer (ou referenciar) a foto/recorte de um móvel, objeto de decoração, luminária, etc. e quiser encontrar produtos iguais ou parecidos à venda — com loja, preço e link — em marketplaces como Mercado Livre, Shopee, Amazon, Magalu. Complementa a skill de redecoração: depois de gerar um mockup, identifica e garimpa os produtos da cena para o cliente comprar. Faz upload da imagem e consulta o Google Lens via SerpApi.
---

# Skill de Busca de Produtos por Imagem

Recebe uma imagem de um produto (foto real, recorte ou um mockup gerado pela skill
**redecoracao**) e descobre **onde comprar** itens iguais/parecidos, retornando **loja,
preço e link** — priorizando marketplaces brasileiros (Mercado Livre, Shopee, Amazon.com.br,
Magalu, Madeira Madeira, Leroy Merlin, Tok&Stok, etc.).

Por baixo, usa a **Google Lens API do SerpApi** (`engine=google_lens`). Como o Google Lens só
aceita imagem por **URL pública**, o script faz o **upload automático** da imagem local antes
de buscar.

## Quando usar
- "Onde compro esse sofá/luminária/tapete?" + uma foto.
- Depois de aprovar um mockup de redecoração: garimpar os produtos da cena para o cliente.
- Achar alternativas mais baratas de um item (busca por similaridade visual).

## Pré-requisitos
- **Chave do SerpApi**: `SERPAPI_API_KEY` no `.env` (ou variável de ambiente), ou via `--api-key`.
  Crie em https://serpapi.com/manage-api-key (plano free ≈ 100 buscas/mês).
- (Opcional) `IMGBB_API_KEY` para hospedar a imagem via imgbb (mais estável). Sem ela, o script
  usa hosts anônimos (catbox.moe → tmpfiles.org) automaticamente.

## Fluxo (siga nesta ordem)

### 1. Obter a imagem
- Se o usuário deu um caminho de arquivo, use-o.
- Se ele **colou a imagem no chat** e ela não está em disco, extraia do transcript da sessão
  (`.jsonl` em `~/.claude/projects/<projeto>/`): pegue o último bloco `type: "image"` com
  `source.data` em base64 e salve em disco antes de buscar. (Mesma técnica da skill `redecoracao`.)

### 2. Isolar UM produto por busca (passo crítico de qualidade)
O Google Lens funciona **muito melhor com um recorte fechado de um único item** do que com a
foto do cômodo inteiro. Antes de buscar:
- **Leia a imagem (Read)** para identificar o item alvo e estimar a caixa que o envolve.
- Recorte com `--crop "esq,topo,dir,baixo"` (pixels), OU use `--auto-crop` para deixar o Google
  detectar o objeto principal. Para vários itens da mesma cena, faça **uma busca por item**.
- Se o usuário já mandou o recorte do produto, pule direto para o passo 3.

### 3. Buscar
```bash
uv run ~/.claude/skills/busca-produtos/scripts/find_products.py \
  --input-image "<imagem-ou-recorte>" \
  --output-dir "<pasta>" \
  --query "<descrição curta do item: ex. 'sofá 3 lugares cinza linho'>" \
  --max 20
```
Opções úteis:
- `--crop "120,340,680,720"` — recorta antes de subir (coords em pixels que você estimou no passo 2).
- `--auto-crop` — Google detecta o objeto principal sozinho.
- `--only-shopping` — descarta blog/Pinterest e mostra só lojas/itens com preço.
- `--type products|visual_matches|exact_matches|all` — default `visual_matches` (rico em loja+preço).
- `--image-url "https://..."` — se a imagem já está hospedada (pula o upload).
- `--country` / `--hl` — default `br` / `pt-br`.

O `--query` ajuda bastante a desambiguar — sempre descreva o item em poucas palavras.

### 4. Apresentar os resultados
O script salva `resultados.json` e `resultados.md` na `--output-dir` e imprime um resumo.
Mostre ao usuário uma **tabela enxuta** (produto · loja · preço · link), destacando os
marketplaces BR (marcados com 🇧🇷/`[BR]`) e os itens com preço. Se vier vazio ou ruim, sugira:
recorte mais fechado, `--auto-crop`, ou um `--query` melhor — e tente de novo.

### 5. Buscar vários itens de uma cena (em paralelo)
Para garimpar uma cena inteira (sofá + luminária + tapete + quadro), identifique cada item no
passo 2 e dispare **uma chamada por item EM PARALELO** (vários Agent/Bash numa só mensagem),
cada um com seu `--crop`/`--query` e uma `--output-dir` própria (ex.: `busca/sofa`, `busca/tapete`).
Depois consolide tudo numa lista de compras única.

## Integração com as outras skills
- **redecoracao** gera o mockup → **busca-produtos** encontra onde comprar os itens do mockup.
- Para o papel de "arquiteto": entregue ao cliente o ambiente redecorado **+** a lista de compras
  com lojas e preços reais.

## Notas
- Busca por imagem retorna **similares**, nem sempre o produto exato — deixe isso claro ao usuário.
- Hosts de upload anônimos têm imagens temporárias; isso não afeta a busca (a URL só precisa
  existir no momento da consulta).
- Custo: cada execução consome 1 busca da cota do SerpApi.
