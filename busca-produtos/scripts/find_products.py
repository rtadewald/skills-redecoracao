# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow",
#     "python-dotenv",
#     "requests",
# ]
# ///
"""
Busca de produtos por imagem (reverse image search) via Google Lens (SerpApi).

Entra com uma imagem (foto/recorte de um produto, ou um mockup de redecoração) e devolve
ONDE COMPRAR produtos iguais/parecidos — com nome da loja, preço e link — priorizando
marketplaces brasileiros (Mercado Livre, Shopee, Amazon.com.br, Magalu, etc.).

COMO FUNCIONA
-------------
1. A imagem local é enviada para um host público (Google Lens só aceita URL de imagem).
   Hosts suportados (sem precisar de chave): catbox.moe e tmpfiles.org. Com IMGBB_API_KEY,
   usa o imgbb (mais estável). Se você já tem a imagem numa URL pública, passe --image-url.
2. Chama a Google Lens API do SerpApi (engine=google_lens) com hl=pt-br & country=br.
3. Normaliza, deduplica e RANQUEIA os resultados (marketplaces BR e itens com preço primeiro).
4. Salva resultados.json + resultados.md na --output-dir e imprime uma tabela no terminal.

USO
---
    uv run find_products.py --input-image recorte_sofa.png --output-dir ./busca \
        --query "sofá 3 lugares cinza" --max 20

    # recortar só o produto antes de buscar (coords em pixels: esq,topo,dir,baixo):
    uv run find_products.py --input-image mockup.png --crop "120,340,680,720" ...

    # imagem já hospedada:
    uv run find_products.py --image-url "https://.../sofa.jpg" ...

CHAVE
-----
SERPAPI_API_KEY no .env / variável de ambiente, ou --api-key. (Plano free do SerpApi:
~100 buscas/mês.) Crie em https://serpapi.com/manage-api-key.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

# Marketplaces / lojas BR priorizados na ordenação (peso maior = aparece antes).
BR_STORES = {
    "mercadolivre": 100, "mercadolibre": 100, "shopee": 98, "amazon.com.br": 95,
    "magazineluiza": 92, "magalu": 92, "americanas": 88, "casasbahia": 88,
    "pontofrio": 85, "madeiramadeira": 90, "leroymerlin": 90, "tokstok": 90,
    "mobly": 88, "westwing": 86, "etna": 84, "camicado": 82, "shoptime": 80,
    "carrefour": 78, "aliexpress": 70, "shein": 68,
}


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- upload hosts
def upload_catbox(path):
    with open(path, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (os.path.basename(path), f)},
            timeout=120,
        )
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox resposta inesperada: {url[:200]}")
    return url


def upload_tmpfiles(path):
    with open(path, "rb") as f:
        r = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (os.path.basename(path), f)},
            timeout=120,
        )
    r.raise_for_status()
    url = r.json()["data"]["url"]
    # converte página de preview -> link direto: tmpfiles.org/123/x -> tmpfiles.org/dl/123/x
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


def upload_imgbb(path, key):
    import base64
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": key, "image": b64},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["data"]["url"]


def host_image(path, prefer=None):
    """Sobe a imagem e devolve URL pública. Tenta hosts em ordem, com fallback."""
    imgbb_key = os.environ.get("IMGBB_API_KEY")
    order = []
    if prefer:
        order.append(prefer)
    if imgbb_key:
        order.append("imgbb")
    order += ["catbox", "tmpfiles"]
    seen, ordered = set(), []
    for h in order:
        if h not in seen:
            seen.add(h); ordered.append(h)

    last_err = None
    for host in ordered:
        try:
            if host == "imgbb":
                if not imgbb_key:
                    continue
                url = upload_imgbb(path, imgbb_key)
            elif host == "catbox":
                url = upload_catbox(path)
            elif host == "tmpfiles":
                url = upload_tmpfiles(path)
            else:
                continue
            log(f"  imagem hospedada em {host}: {url}")
            return url
        except Exception as e:  # tenta o próximo host
            last_err = e
            log(f"  host {host} falhou: {e}")
    raise RuntimeError(f"Não consegui hospedar a imagem em nenhum host. Último erro: {last_err}")


# ---------------------------------------------------------------- crop helper
def crop_image(path, crop_spec, out_dir):
    from PIL import Image
    try:
        l, t, r, b = (int(x.strip()) for x in crop_spec.split(","))
    except Exception:
        raise SystemExit('ERRO: --crop deve ser "esq,topo,dir,baixo" em pixels. Ex.: "120,340,680,720"')
    img = Image.open(path).convert("RGB")
    cropped = img.crop((l, t, r, b))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "_recorte.png")
    cropped.save(out)
    log(f"  recorte salvo: {out} ({cropped.width}x{cropped.height})")
    return out


# ---------------------------------------------------------------- serpapi
def google_lens(image_url, api_key, search_type, country, hl, query, auto_crop):
    params = {
        "engine": "google_lens",
        "url": image_url,
        "type": search_type,
        "country": country,
        "hl": hl,
        "api_key": api_key,
    }
    if query:
        params["q"] = query
    if auto_crop:
        params["auto_crop"] = "true"
    r = requests.get(SERPAPI_ENDPOINT, params=params, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"SerpApi HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"SerpApi error: {data['error']}")
    return data


def collect_matches(data):
    """Junta candidatos das várias chaves possíveis da resposta."""
    out = []
    for key in ("products", "shopping_results", "exact_matches", "visual_matches"):
        for item in (data.get(key) or []):
            item = dict(item)
            item["_section"] = key
            out.append(item)
    return out


def store_weight(source, link):
    hay = f"{(source or '').lower()} {(link or '').lower()}"
    best = 0
    for needle, w in BR_STORES.items():
        if needle in hay:
            best = max(best, w)
    return best


def normalize(item):
    price = item.get("price")
    price_str, price_val = None, None
    if isinstance(price, dict):
        price_str = price.get("value")
        price_val = price.get("extracted_value")
    elif isinstance(price, (int, float, str)):
        price_str = str(price)
    link = item.get("link")
    source = item.get("source")
    if not source and link:
        source = urlparse(link).netloc.replace("www.", "")
    return {
        "title": item.get("title"),
        "source": source,
        "link": link,
        "price": price_str,
        "price_value": price_val,
        "in_stock": item.get("in_stock"),
        "condition": item.get("condition"),
        "rating": item.get("rating"),
        "reviews": item.get("reviews"),
        "thumbnail": item.get("thumbnail"),
        "section": item.get("_section"),
        "_store_weight": store_weight(source, link),
    }


def rank_and_dedupe(items, only_shopping):
    seen, uniq = set(), []
    for it in items:
        n = normalize(it)
        link = n["link"]
        if not link or link in seen:
            continue
        seen.add(link)
        uniq.append(n)
    if only_shopping:
        uniq = [n for n in uniq if n["_store_weight"] > 0 or n["price"]]
    # ordena: loja BR (peso) desc, depois com preço, depois com avaliação
    uniq.sort(key=lambda n: (
        n["_store_weight"],
        1 if n["price"] else 0,
        n["reviews"] or 0,
    ), reverse=True)
    return uniq


# ---------------------------------------------------------------- output
def write_markdown(results, out_path, image_url, query):
    lines = ["# Onde comprar — resultados da busca por imagem", ""]
    if query:
        lines.append(f"**Busca textual:** {query}  ")
    lines.append(f"**Imagem pesquisada:** {image_url}  ")
    lines.append(f"**Total de resultados:** {len(results)}")
    lines.append("")
    lines.append("| # | Produto | Loja | Preço | Link |")
    lines.append("|---|---------|------|-------|------|")
    for i, n in enumerate(results, 1):
        title = (n["title"] or "—").replace("|", "\\|")[:80]
        store = (n["source"] or "—").replace("|", "\\|")
        flag = " 🇧🇷" if n["_store_weight"] > 0 else ""
        price = n["price"] or "—"
        link = n["link"] or ""
        lines.append(f"| {i} | {title} | {store}{flag} | {price} | [abrir]({link}) |")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    p = argparse.ArgumentParser(description="Busca produtos por imagem via Google Lens (SerpApi)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input-image", help="Caminho da imagem local a pesquisar")
    src.add_argument("--image-url", help="URL pública de uma imagem já hospedada")
    p.add_argument("--output-dir", default=".", help="Pasta para salvar resultados.json/.md")
    p.add_argument("--query", "-q", default=None, help="Texto opcional p/ refinar (ex.: 'sofá cinza 3 lugares')")
    p.add_argument("--crop", default=None, help='Recortar antes de buscar: "esq,topo,dir,baixo" em pixels')
    p.add_argument("--type", default="visual_matches",
                   choices=["visual_matches", "products", "exact_matches", "all"],
                   help="Tipo de busca do Google Lens (default: visual_matches)")
    p.add_argument("--country", default="br", help="Código do país (default: br)")
    p.add_argument("--hl", default="pt-br", help="Idioma (default: pt-br)")
    p.add_argument("--max", type=int, default=20, help="Máx. de resultados (default: 20)")
    p.add_argument("--only-shopping", action="store_true",
                   help="Só itens de lojas conhecidas ou com preço")
    p.add_argument("--auto-crop", action="store_true",
                   help="Deixa o Google detectar o objeto principal automaticamente")
    p.add_argument("--upload-host", default=None, choices=["catbox", "tmpfiles", "imgbb"],
                   help="Forçar host de upload (default: tenta imgbb→catbox→tmpfiles)")
    p.add_argument("--api-key", default=None, help="Sobrescreve SERPAPI_API_KEY")
    args = p.parse_args()

    api_key = args.api_key or os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        print("ERRO: defina SERPAPI_API_KEY (.env/env) ou passe --api-key. "
              "Crie em https://serpapi.com/manage-api-key", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # 1) obter URL pública da imagem
    if args.image_url:
        image_url = args.image_url
    else:
        if not os.path.isfile(args.input_image):
            print(f"ERRO: imagem não encontrada: {args.input_image}", file=sys.stderr)
            sys.exit(1)
        path = args.input_image
        if args.crop:
            path = crop_image(path, args.crop, args.output_dir)
        log("Hospedando imagem...")
        try:
            image_url = host_image(path, prefer=args.upload_host)
        except Exception as e:
            print(f"ERRO: {e}", file=sys.stderr)
            sys.exit(1)

    # 2) buscar
    log(f"Buscando no Google Lens (type={args.type}, country={args.country})...")
    try:
        data = google_lens(image_url, api_key, args.type, args.country, args.hl,
                            args.query, args.auto_crop)
    except Exception as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)

    matches = collect_matches(data)
    # fallback: se pediu products/exact e veio vazio, tenta visual_matches
    if not matches and args.type != "visual_matches":
        log("Sem resultados nesse tipo; tentando visual_matches...")
        try:
            data = google_lens(image_url, api_key, "visual_matches", args.country,
                               args.hl, args.query, args.auto_crop)
            matches = collect_matches(data)
        except Exception:
            pass

    results = rank_and_dedupe(matches, args.only_shopping)[: args.max]

    if not results:
        print("Nenhum resultado encontrado. Tente um recorte mais fechado do produto "
              "(--crop), use --auto-crop, ou adicione --query.", file=sys.stderr)

    # 3) salvar
    json_path = os.path.join(args.output_dir, "resultados.json")
    md_path = os.path.join(args.output_dir, "resultados.md")
    with open(json_path, "w") as f:
        json.dump({"image_url": image_url, "query": args.query,
                   "count": len(results), "results": results}, f,
                  ensure_ascii=False, indent=2)
    write_markdown(results, md_path, image_url, args.query)

    # 4) imprimir resumo no stdout (o agente lê isto)
    print(f"\n{len(results)} resultado(s) — imagem: {image_url}")
    print(f"JSON: {json_path}\nMarkdown: {md_path}\n")
    for i, n in enumerate(results, 1):
        flag = " [BR]" if n["_store_weight"] > 0 else ""
        price = f" — {n['price']}" if n["price"] else ""
        print(f"{i}. {n['source']}{flag}{price}\n   {n['title']}\n   {n['link']}")


if __name__ == "__main__":
    main()
