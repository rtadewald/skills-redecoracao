---
name: redecoracao
description: Redecora ambientes a partir de uma foto, gerando 10 mockups variados de uma vez. Use quando o usuário fornecer (ou referenciar) a foto de um cômodo e quiser propostas de redecoração, variações de estilo ou mockups para clientes/arquitetos. Fluxo ágil: analisa a foto, faz no máximo 1 pergunta de briefing (se faltar contexto) e então gera 10 variações bem diversas — em complexidade (pouca→muita mudança), tonalidade (clara/média/escura) e estilo — em paralelo via subagents. Preserva sempre o layout (paredes, janelas, ângulo, pessoas), mudando só acabamentos, cores, objetos, iluminação e (em níveis altos) mobília. Usa a skill nano-banana-pro para gerar as imagens.
---

# Skill de Redecoração de Ambientes

Gera mockups de redecoração a partir da foto de um ambiente real, aplicando estilos de
arquitetura/decoração. **O LAYOUT é sempre preservado** (paredes, posição/tamanho de janelas e
portas, ângulo/enquadramento da câmera e a pessoa). O que a IA pode mexer depende do **nível de
complexidade** escolhido com o usuário. As imagens são geradas pela skill **nano-banana-pro** e
salvas com numeração de versão para permitir iteração.

## Níveis de complexidade (operações permitidas)
- **Leve** — pintar paredes; trocar/adicionar objetos, quadros e plantas; trocar/adicionar
  iluminação (abajures, luminárias, LEDs). **Mantém todos os móveis.**
- **Médio** — tudo do Leve + trocar/adicionar cortinas e persianas (nas aberturas existentes) +
  instalar/trocar revestimentos de superfície (painel, papel de parede, piso). **Mantém os móveis.**
- **Agressivo** — tudo do Médio + **adicionar e remover/trocar mobília** (fixa ou móvel).
- **Em qualquer nível** é PROIBIDO mexer no layout: mover/criar/remover paredes, e mover,
  redimensionar ou trocar de lugar janelas e portas.

## Estrutura de pastas do projeto (use SEMPRE)
Ao começar um projeto, crie UMA pasta de trabalho e **fique só nela**. Convenção:
```
~/redecoracao/<slug-do-projeto>/      ← raiz do projeto (--output-dir aponta aqui)
├── _original.<ext>                   ← foto original
├── manifest.json                     ← histórico de TODAS as versões (numeração global)
├── exploracao/                       ← versões iniciais (lotes de 10)  [--subdir exploracao]
│   ├── v01-japandi.png ...
├── edicoes/                          ← reiterações sobre uma versão     [--subdir edicoes]
│   ├── v11-contemporaneo.png ...
├── aprovado/                         ← cenário final aprovado (Pro/2K-4K)
│   └── final-<estilo>.png
└── produtos/                         ← itens isolados em fundo branco p/ busca de compra
    ├── produto-01-*.png ... + products.json
```
- `--output-dir` = **raiz do projeto** (o `manifest.json` e o `_plan.json` ficam aqui).
- `--subdir` = a fase: `exploracao` (padrão), `edicoes`, etc. A numeração `vNN` é **global** ao
  projeto (continua entre fases); o `manifest.json` guarda `parent_version` para a linhagem.
- Slug do projeto: derive do cômodo/objetivo (ex.: `quarto-estudio`, `sala-cliente-joao`).

## Fluxo (siga nesta ordem)

### 1. Criar o projeto e obter a foto
- Defina a raiz `~/redecoracao/<slug>/` e trabalhe somente dentro dela.
- Se o usuário deu um caminho de arquivo, use-o como entrada (copie/derive para a raiz se quiser).
- Se o usuário **colou a imagem no chat** e ela não está em disco, extraia-a do transcript
  da sessão (arquivo `.jsonl` em `~/.claude/projects/<projeto>/`): procure o último bloco
  `type: "image"` com `source.data` em base64 e salve em `<raiz>/_original.<ext>`.
- Confirme visualmente (Read) que é a foto certa antes de prosseguir.

### 2. Análise rápida + briefing (no máximo 1 pergunta) — seja ÁGIL
- Olhe a foto (Read) rapidamente — leitura **interna**, SEM diagnóstico longo no chat.
- Se o usuário **já deu um briefing** (que cômodo é + o que espera), use-o e vá direto ao passo 3.
- Se faltar, faça **UMA única pergunta curta** (AskUserQuestion, 1 pergunta): "Que cômodo é este e
  o que você espera dele?" — com 3-4 opções comuns + Outro.
- **NÃO** pergunte nível, tonalidade, paleta nem estilo — a variedade é VOCÊ que define no passo 3.

### 3. Montar 10 combos BEM VARIADOS e gerar os 10 EM PARALELO
Com o briefing, **escolha você 10 combinações deliberadamente diversas**. Varie de propósito:
- **Complexidade** (`level`): misture `leve`, `medio` e `agressivo` (de pouca a muita mudança).
- **Tonalidade**: inclua versões claras/neutras, médias e escuras.
- **Estilos**: 10 estilos distintos do catálogo, cobrindo direções diferentes (`--list` mostra todos).

**(a) Planejar** com `--combos` (10 itens num JSON; cada item vira 1 imagem):
```bash
uv run ~/.claude/skills/redecoracao/scripts/redecorate.py --plan \
  --input-image "<raiz>/_original.<ext>" --output-dir "<raiz>" --subdir exploracao --resolution 1K \
  --room "<cômodo>" --objective "<o que o usuário espera>" \
  --notes "<elementos fixos a preservar NESTA foto>" \
  --combos '[{"style":"japandi","level":"leve","tonality":"claro"},{"style":"warm-minimalism","level":"medio","tonality":"medio"},{"style":"industrial","level":"medio","tonality":"escuro"}, ... 10 itens ...]'
```
O comando imprime os índices (`[0]`..`[9]`).

**(b) Disparar os 10 subagents EM PARALELO** — UMA única mensagem com **10 chamadas do Agent**,
cada uma executando só um job (nada além do comando):
```bash
uv run ~/.claude/skills/redecoracao/scripts/redecorate.py --run-job "<pasta>/_plan.json" <índice>
```

**(c) Finalizar**:
```bash
uv run ~/.claude/skills/redecoracao/scripts/redecorate.py --finalize "<pasta>/_plan.json"
```

- Resolução default **1K** (rápido/barato; upscale ou 2K/4K só na versão aprovada).
- Proporção detectada e travada automaticamente.
- **Sempre** passe em `--notes` os elementos fixos críticos da foto (ex.: "preservar a segunda
  pessoa no canto", "manter o painel de LED à direita", "não há janela").

### 4. Mostrar as 10 no chat
- Exiba (Read) as 10 imagens **no chat**, agrupadas por tonalidade (claras / médias / escuras) ou
  complexidade, dizendo o **estilo + nível** de cada uma e comentando brevemente a fidelidade.

### 5. Iterar sobre versões que o usuário gostou (fase de edição)
Quando o usuário disser "gostei da vX, mas mude Y", itere usando aquela versão como nova
entrada (o output vira o novo input), salvando na subpasta `edicoes/`:

```bash
uv run ~/.claude/skills/redecoracao/scripts/redecorate.py \
  --output-dir "<raiz>" --subdir edicoes \
  --base-version <X> --styles "<mesmo-estilo>" \
  --notes "<o que mudar: ex. iluminação mais quente, adicionar tapete>"
```
O `manifest.json` guarda `parent_version` para rastrear a linhagem das iterações.
(Para gerar N variações novas a partir da ORIGINAL no mesmo estilo, use `--combos` com vários
itens daquele estilo, `--subdir edicoes`, e `--input-image` apontando para `_original`.)

### 6. Versão APROVADA → comparação e extração de produtos
Quando o usuário **aprovar um cenário**:

**(a) Finalize em alta qualidade** (opcional, recomendado): regenere a versão aprovada em **Pro +
2K/4K** e salve em `aprovado/final-<estilo>.png` (use `--base-version` da aprovada ou regenere com o
mesmo prompt; `--provider openrouter --model google/gemini-3-pro-image-preview --resolution 2K`).

**(b) Compare original × aprovada e liste o que foi ADICIONADO.** Abra as duas (Read) e produza no
chat uma lista **completa** de tudo que entrou na cena: mobília, decoração, cortinas/persianas,
plantas e vasos, luminárias, tapetes, quadros/arte, itens de mesa/eletrônicos, etc. Para cada item,
escreva um nome curto + uma descrição objetiva (tipo, cor, material, formato) — essa descrição vira
o prompt do produto. **Peça a aprovação do usuário** nessa lista antes de gerar.

**(c) Gere cada item isolado em FUNDO BRANCO** (foto de produto p/ a busca de compra), em paralelo:
```bash
uv run ~/.claude/skills/redecoracao/scripts/extract_products.py \
  --input-image "<raiz>/aprovado/final-<estilo>.png" \
  --output-dir "<raiz>/produtos" \
  --items '[{"name":"Mesa em L","desc":"mesa em L de madeira nogueira com estrutura de metal preto"},{"name":"Cadeira de escritório","desc":"cadeira ergonômica preta com tela mesh"}, ...]'
```
Gera `produto-NN-*.png` (fundo branco, 1:1) + `produtos/products.json`. O `extract_products.py`
**sempre roda os itens EM PARALELO** (e em baixa resolução por padrão — 1K). Exiba os recortes no chat.

**(d) Busque onde comprar.** Para cada produto gerado, acione a skill **busca-produtos** (Google
Lens via SerpAPI) passando a imagem em fundo branco, para retornar lojas/preços/links (Mercado
Livre, Shopee, Amazon, Magalu).

## Regras importantes
- **Seja ágil**: no máximo **1 pergunta** (briefing), e só se o usuário não tiver dado o contexto.
  Sem diagnóstico longo, sem perguntar nível/tonalidade/paleta/estilo.
- **Você** define a variedade: **sempre 10 combos** bem distintos (complexidade + tonalidade +
  estilo), gerados **em paralelo via subagents** (passo 3) e exibidos no chat (passo 4).
- **A CÂMERA é SEMPRE fixa** — em TODOS os níveis (inclusive `reforma`): mesmo ângulo, altura,
  distância, zoom e enquadramento da foto original. Nunca gire/afaste/aproxime/reposicione a câmera.
- **O LAYOUT é imutável** nos níveis `leve`/`medio`/`agressivo`: paredes e a **posição/tamanho de
  janelas e portas**. Mobília só muda no `agressivo`. O nível **`reforma`** é o ÚNICO que permite
  obra estrutural — e SÓ a explicitamente pedida (ex.: janela→porta de vidro), mantendo a câmera e a
  planta geral. O preâmbulo do script impõe isso conforme o `--level` de cada combo.
- **Trabalhe sempre dentro da pasta do projeto** (`~/redecoracao/<slug>/`), com as subpastas
  `exploracao/`, `edicoes/`, `aprovado/`, `produtos/`. Não espalhe arquivos fora dela.
- **Ao aprovar um cenário** (passo 6): compare com a original, liste tudo que foi ADICIONADO,
  confirme com o usuário, gere cada item em **fundo branco** (`extract_products.py`) e then acione a
  skill **busca-produtos** para achar onde comprar.
- O ponto mais frágil é a **fidelidade do rosto** de pessoas na foto; avise o usuário quando
  a semelhança variar e, se for crítico, sugira gerar só o ambiente.
- O catálogo de estilos vive em `styles.json` e é **expansível** — adicione novos estilos
  no mesmo formato (`id`, `name`, `category`, `tonality`, `prompt`).

## Modelo e provider
- O gerador suporta dois backends, via `--provider`:
  - `openrouter` (**padrão**) — uma chave dá acesso a vários modelos. Requer `OPENROUTER_API_KEY`.
    Modelos: `google/gemini-3-pro-image-preview` (Pro, padrão) ou `google/gemini-3.1-flash-image-preview` (Flash).
  - `gemini` — API do Google direto. Requer `GEMINI_API_KEY`.
    Modelos: `gemini-3-pro-image-preview` (Pro) ou `gemini-3.1-flash-image-preview` (Flash).
- **Padrão: Flash + 1K** (rápido e barato, ideal para gerar 10 de uma vez). Para a versão final
  aprovada, regenere em **Pro** (`--model google/gemini-3-pro-image-preview`) e 2K/4K.

## Pré-requisitos
- Skill **nano-banana-pro** instalada (em `~/.claude/skills/nano-banana-pro/`).
- Chave do provider escolhido no `.env` (ou variável de ambiente): `OPENROUTER_API_KEY`
  (padrão) e/ou `GEMINI_API_KEY`.
