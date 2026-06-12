# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Gera fotos de PRODUTO ISOLADO em FUNDO BRANCO a partir de um cenário aprovado, para alimentar a
busca reversa (skill busca-produtos / SerpAPI Google Lens) e descobrir onde comprar cada item.

Recebe a imagem aprovada + uma lista de itens (JSON) e gera, EM PARALELO, uma foto estilo
e-commerce de cada item (fundo branco, item centralizado), salvando em <output-dir>/ e registrando
um products.json.

Uso:
    uv run extract_products.py \
        --input-image aprovado.png \
        --output-dir "<projeto>/produtos" \
        --items '[{"name":"Mesa em L","desc":"mesa em L de madeira nogueira com estrutura preta"}, ...]' \
        [--resolution 1K|2K] [--provider openrouter|gemini] [--model ...]
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NANO_SCRIPT = os.path.expanduser(
    os.environ.get("NANO_BANANA_SCRIPT", "~/.claude/skills/nano-banana-pro/scripts/generate_image.py")
)

PROMPT_TMPL = (
    "Foto de PRODUTO para catálogo de e-commerce. Mostre APENAS este item, isolado e inteiro: {desc}. "
    "Use como referência o mesmo item que aparece na imagem fornecida (mesmo tipo, cor, material, "
    "formato e proporções). FUNDO BRANCO PURO (#FFFFFF), item centralizado e totalmente dentro do "
    "quadro, iluminação de estúdio suave e uniforme, sombra de contato sutil, SEM texto, SEM marca "
    "d'água, SEM pessoas e SEM nenhum outro objeto ou cenário. Estilo de foto de produto "
    "(Mercado Livre / Shopee / Amazon), nítida e realista."
)


def slug(s):
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)[:40] or "item"


def gen_one(input_image, out_path, prompt, resolution, provider, model):
    cmd = ["uv", "run", NANO_SCRIPT, "--prompt", prompt, "--filename", out_path,
           "--resolution", resolution, "--aspect-ratio", "1:1",
           "--input-image", input_image, "--provider", provider]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and os.path.isfile(out_path), proc.stderr.strip() or proc.stdout.strip()


def main():
    p = argparse.ArgumentParser(description="Gera fotos de produto isolado em fundo branco")
    p.add_argument("--input-image", required=True, help="Cenário aprovado (referência dos itens)")
    p.add_argument("--output-dir", required=True, help="Pasta de saída (ex.: <projeto>/produtos)")
    p.add_argument("--items", required=True, help="JSON (string ou arquivo): [{\"name\":..,\"desc\":..}]")
    p.add_argument("--resolution", default="1K", choices=["1K", "2K", "4K"])
    p.add_argument("--provider", default="openrouter", choices=["openrouter", "gemini"])
    p.add_argument("--model", default=None)
    args = p.parse_args()

    raw = args.items.strip()
    items = json.loads(raw) if raw.startswith("[") else json.load(open(raw, encoding="utf-8"))
    os.makedirs(args.output_dir, exist_ok=True)

    jobs = []
    for i, it in enumerate(items, 1):
        name = it.get("name") or it.get("desc", f"item {i}")
        desc = it.get("desc") or name
        out = os.path.join(args.output_dir, f"produto-{i:02d}-{slug(name)}.png")
        jobs.append({"i": i, "name": name, "desc": desc, "out": out,
                     "prompt": PROMPT_TMPL.format(desc=desc)})

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(jobs))) as ex:
        futs = {ex.submit(gen_one, args.input_image, j["out"], j["prompt"],
                          args.resolution, args.provider, args.model): j for j in jobs}
        for fut in concurrent.futures.as_completed(futs):
            j = futs[fut]
            ok, msg = fut.result()
            results[j["i"]] = ok
            print(f"  [{'OK' if ok else 'FALHOU'}] produto {j['i']:02d} — {j['name']}")
            if not ok:
                print(f"        {msg}", file=sys.stderr)

    catalog = [{"name": j["name"], "desc": j["desc"], "file": j["out"], "ok": results[j["i"]]}
               for j in jobs]
    with open(os.path.join(args.output_dir, "products.json"), "w", encoding="utf-8") as f:
        json.dump({"input_image": args.input_image, "products": catalog}, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for v in results.values() if v)
    print(f"\nConcluído: {ok_count}/{len(jobs)} produtos em {args.output_dir}")
    print(f"Catálogo: {os.path.join(args.output_dir, 'products.json')}")


if __name__ == "__main__":
    main()
