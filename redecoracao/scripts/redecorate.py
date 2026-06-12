# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow",
# ]
# ///
"""
Orquestrador da skill de redecoração.

Monta o prompt (preâmbulo de preservação FORTE + contexto + estilo), detecta e TRAVA a
proporção da foto, e gera as imagens via skill nano-banana-pro. Salva com numeração de
versão (v01, v02, ...) e registra tudo em manifest.json para permitir iteração.

MODOS DE USO
------------
1) Direto (gera tudo de uma vez, em paralelo por threads):
    uv run redecorate.py --input-image foto.jpg --output-dir ./proj \
        --styles japandi,industrial,mid-century,minimalista,dark-moody \
        --resolution 2K --tonality claro --palette "madeira e verde" --density equilibrada

2) Via subagents (planejar -> rodar cada job num subagent -> finalizar):
    a. uv run redecorate.py --plan  --input-image foto.jpg --output-dir ./proj --styles a,b,c,d,e [contexto...]
       -> escreve ./proj/_plan.json com os jobs numerados (NÃO gera nada).
    b. para cada índice i (0..N-1), um subagent roda:
       uv run redecorate.py --run-job ./proj/_plan.json i
    c. uv run redecorate.py --finalize ./proj/_plan.json
       -> registra no manifest.json os jobs cujas imagens existem.

3) Listar estilos:  uv run redecorate.py --list

4) Iterar sobre uma versão:  --base-version N (usa a saída dela como nova entrada).
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
STYLES_PATH = os.path.join(SKILL_DIR, "styles.json")
NANO_SCRIPT = os.path.expanduser(
    os.environ.get("NANO_BANANA_SCRIPT",
                   "~/.claude/skills/nano-banana-pro/scripts/generate_image.py")
)

# Backend padrão: OpenRouter (uma única chave dá acesso a vários modelos). Requer
# OPENROUTER_API_KEY. Para usar a API do Gemini direto: --provider gemini (requer GEMINI_API_KEY).
DEFAULT_PROVIDER = "openrouter"
# Modelo padrão: Nano Banana 2 (Gemini 3.1 Flash Image) — rápido e barato. Para qualidade/
# fidelidade máxima numa versão aprovada, troque para a Pro via --model.
#   OpenRouter: google/gemini-3.1-flash-image-preview | google/gemini-3-pro-image-preview (Pro)
#   Gemini    : gemini-3.1-flash-image-preview        | gemini-3-pro-image-preview (Pro)
DEFAULT_MODEL = {
    "openrouter": "google/gemini-3.1-flash-image-preview",
    "gemini": "gemini-3.1-flash-image-preview",
}

SUPPORTED_ASPECTS = {
    "1:1": 1 / 1, "2:3": 2 / 3, "3:2": 3 / 2, "3:4": 3 / 4, "4:3": 4 / 3,
    "4:5": 4 / 5, "5:4": 5 / 4, "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9,
}

# Trava de layout — INVIOLÁVEL em TODOS os níveis. O que varia entre níveis é só o conjunto de
# operações permitidas (ver LEVELS/OP_CLAUSES). A arquitetura e o enquadramento nunca mudam.
PREAMBLE_INTRO = (
    "INSTRUÇÃO DE EDIÇÃO — você vai REDECORAR um ambiente real a partir desta fotografia, "
    "respeitando estritamente os limites abaixo."
)

# Trava de câmera — ABSOLUTA, vale para TODOS os níveis (inclusive reforma). A câmera é fixa.
CAMERA_LOCK = (
    " TRAVA DE CÂMERA (regra absoluta, vale para TODOS os níveis, inclusive reforma): a câmera é "
    "FIXA. Mantenha EXATAMENTE o mesmo ângulo, altura, distância, lente, zoom e enquadramento da "
    "foto original — a MESMA porção do cômodo visível e a MESMA perspectiva das paredes, do teto e "
    "do piso. NÃO gire, NÃO afaste, NÃO aproxime, NÃO endireite, NÃO nivele e NÃO reposicione a "
    "câmera. É como se a nova foto fosse tirada do mesmíssimo tripé, no mesmo instante."
)

LAYOUT_LOCK = (
    " REGRA INVIOLÁVEL (vale para qualquer nível) — o LAYOUT e a ARQUITETURA NÃO podem mudar de "
    "forma alguma: não mova, crie, remova ou redimensione paredes, divisórias, colunas, teto ou "
    "piso; não mova, redimensione, crie nem remova JANELAS e PORTAS — cada abertura permanece no "
    "MESMO lugar, tamanho e formato. Não mude o ângulo, a distância, o zoom nem o enquadramento da "
    "câmera. Não altere a PESSOA (rosto, cabelo, barba, expressão, roupa e logos, pose) nem o "
    "microfone de lapela. O ponto de vista e a estrutura são exatamente os da foto original."
)

# Trava relaxada — usada SÓ no nível 'reforma'. Permite obra estrutural solicitada, mas preserva
# o enquadramento e a planta geral, para o resultado ainda ser o MESMO cômodo, do mesmo ponto.
LAYOUT_LOCK_REFORMA = (
    " REGRA DE ENQUADRAMENTO (nível reforma) — mantenha o MESMO ponto de vista, ângulo e "
    "enquadramento da câmera e as DIMENSÕES e proporções gerais do cômodo (mesmo formato de planta, "
    "mesma perspectiva, mesmas paredes principais nas mesmas posições). Você PODE executar as "
    "modificações ESTRUTURAIS explicitamente pedidas no contexto (ex.: transformar a janela em porta "
    "de vidro/sacada), mas NÃO invente mudanças estruturais não solicitadas e NÃO reposicione as "
    "paredes nem o ponto de vista. O resultado deve ser claramente o MESMO cômodo, reformado."
)

# Cada operação que a IA pode ter permissão para fazer.
OP_CLAUSES = {
    "paint": "pintar/repintar as paredes e superfícies existentes (cor e acabamento)",
    "decor": "trocar e adicionar objetos decorativos, quadros, plantas e vasos",
    "lighting": "trocar e adicionar iluminação — abajures, luminárias, fitas de LED, spots — ajustando cor e intensidade",
    "curtains": "trocar ou adicionar cortinas e persianas nas aberturas que JÁ existem (sem criar novas janelas)",
    "fixtures": "instalar ou trocar revestimentos/acabamentos de superfície fixos (ex.: painel ripado, papel de parede, revestimento de piso), SEM mudar a estrutura",
    "furniture": "adicionar mobília nova (fixa ou móvel) e remover ou trocar a mobília atual",
    "structure": "realizar as modificações ESTRUTURAIS explicitamente solicitadas no contexto "
                 "(ex.: transformar uma janela em porta de vidro/sacada, abrir um vão, criar um "
                 "nicho), mantendo o mesmo ponto de vista da câmera e as dimensões gerais do cômodo",
}

# Níveis de complexidade -> operações liberadas.
# 'reforma' é o único que afrouxa a trava de layout (permite obra estrutural solicitada).
LEVELS = {
    "leve":      ["paint", "decor", "lighting"],
    "medio":     ["paint", "decor", "lighting", "curtains", "fixtures"],
    "agressivo": ["paint", "decor", "lighting", "curtains", "fixtures", "furniture"],
    "reforma":   ["paint", "decor", "lighting", "curtains", "fixtures", "furniture", "structure"],
}


def build_permissions(level, ops_override=None):
    ops = ops_override or LEVELS.get(level, LEVELS["leve"])
    allowed = "; ".join(OP_CLAUSES[o] for o in ops if o in OP_CLAUSES)
    txt = f" VOCÊ PODE (nível '{level}'): {allowed}."
    if "furniture" not in ops:
        txt += (" Mantenha TODOS os móveis e equipamentos existentes exatamente onde estão (mesa, "
                "bancada, monitor, notebook, teclado, mouse, luminárias, vasos) — não mova, troque, "
                "adicione nem remova móveis ou eletrônicos.")
    else:
        txt += (" Você pode adicionar, remover ou trocar móveis livremente, desde que respeite a "
                "REGRA INVIOLÁVEL de layout/arquitetura e mantenha coerência com a pessoa e os "
                "equipamentos que ela usa em primeiro plano.")
    return ops, txt

TONALITY_HINT = {
    "claro": "Use uma tonalidade geral CLARA e luminosa.",
    "medio": "Use uma tonalidade geral MÉDIA e equilibrada.",
    "escuro": "Use uma tonalidade geral ESCURA e intimista.",
}

DENSITY_HINT = {
    "minimalista": "Composição MINIMALISTA: poucos objetos, superfícies limpas, bastante espaço vazio.",
    "equilibrada": "Composição equilibrada: nem vazia nem sobrecarregada.",
    "completo": "Composição COMPLETA e rica: muitas camadas, vários objetos decorativos, ambiente cheio e acolhedor.",
}


def load_styles():
    with open(STYLES_PATH, "r", encoding="utf-8") as f:
        return {s["id"]: s for s in json.load(f)["styles"]}


def detect_aspect_ratio(image_path):
    with Image.open(image_path) as im:
        w, h = im.size
    ratio = w / h
    return min(SUPPORTED_ASPECTS.items(), key=lambda kv: abs(kv[1] - ratio))[0]


def manifest_path(output_dir):
    return os.path.join(output_dir, "manifest.json")


def load_manifest(output_dir):
    p = manifest_path(output_dir)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"entries": []}


def save_manifest(output_dir, manifest):
    with open(manifest_path(output_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def next_version(manifest):
    return 1 if not manifest["entries"] else max(e["version"] for e in manifest["entries"]) + 1


def build_prompt(style, ctx):
    ops, permissions = build_permissions(ctx.get("level", "leve"))

    bits = []
    if ctx.get("room"):
        bits.append(f"Tipo de ambiente: {ctx['room']}.")
    if ctx.get("objective"):
        bits.append(f"Objetivo do ambiente: {ctx['objective']}.")
    if ctx.get("palette"):
        bits.append(f"Paleta de cores desejada: {ctx['palette']}.")
    if ctx.get("tonality") in TONALITY_HINT:
        bits.append(TONALITY_HINT[ctx["tonality"]])
    if ctx.get("density") in DENSITY_HINT:
        bits.append(DENSITY_HINT[ctx["density"]])
    if ctx.get("notes"):
        bits.append(f"Observações adicionais: {ctx['notes']}.")
    context = (" CONTEXTO: " + " ".join(bits)) if bits else ""

    if "furniture" in ops:
        style_block = (
            " DIREÇÃO DE ESTILO — leve o ambiente em direção ao estilo a seguir usando as operações "
            "permitidas acima (inclusive trocar/adicionar móveis), sempre respeitando a REGRA "
            f"INVIOLÁVEL de layout/arquitetura. Estilo: {style['prompt']}"
        )
    else:
        style_block = (
            " DIREÇÃO DE ESTILO — aproxime a decoração do estilo a seguir aplicando-o APENAS pelas "
            "operações permitidas acima. Use a descrição como inspiração de PALETA, MATERIAIS e "
            "CLIMA; NÃO insira os móveis citados se não existem na foto e NÃO mova os móveis "
            f"existentes. Estilo: {style['prompt']}"
        )
    lock = LAYOUT_LOCK_REFORMA if ctx.get("level") == "reforma" else LAYOUT_LOCK
    return f"{PREAMBLE_INTRO}{CAMERA_LOCK}{lock}{permissions}{context}{style_block}"


def build_jobs(output_dir, chosen_styles, ctx, start_version):
    jobs = []
    ver = start_version
    for s in chosen_styles:
        jobs.append({
            "version": ver, "style": s["id"], "style_name": s["name"],
            "out_path": os.path.join(output_dir, f"v{ver:02d}-{s['id']}.png"),
            "prompt": build_prompt(s, ctx), "ctx": ctx,
        })
        ver += 1
    return jobs


# Campos que um combo pode sobrescrever por job.
COMBO_FIELDS = ("level", "tonality", "palette", "density", "notes", "room", "objective")


def build_jobs_from_combos(output_dir, combos, base_ctx, styles, start_version):
    """combos: lista de dicts com pelo menos 'style' e, opcionalmente, level/tonality/palette/etc.
    Cada job tem seu PRÓPRIO contexto (variação de nível/tonalidade/estilo por imagem)."""
    jobs = []
    ver = start_version
    for c in combos:
        s = styles.get(c.get("style"))
        if not s:
            print(f"AVISO: estilo desconhecido ignorado no combo: {c.get('style')}", file=sys.stderr)
            continue
        ctx = dict(base_ctx)
        for k in COMBO_FIELDS:
            if c.get(k) is not None:
                ctx[k] = c[k]
        jobs.append({
            "version": ver, "style": s["id"], "style_name": s["name"],
            "out_path": os.path.join(output_dir, f"v{ver:02d}-{s['id']}.png"),
            "prompt": build_prompt(s, ctx), "ctx": ctx,
        })
        ver += 1
    return jobs


def generate_one(input_image, out_path, resolution, aspect, prompt, model, provider):
    cmd = [
        "uv", "run", NANO_SCRIPT,
        "--prompt", prompt,
        "--filename", out_path,
        "--resolution", resolution,
        "--aspect-ratio", aspect,
        "--input-image", input_image,
        "--provider", provider,
        "--model", model,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = proc.returncode == 0 and os.path.isfile(out_path)
    return ok, proc.stdout.strip(), proc.stderr.strip()


def resolve_styles(styles, styles_arg):
    if styles_arg.strip().lower() == "all":
        return list(styles.values())
    chosen = []
    for sid in [s.strip() for s in styles_arg.split(",") if s.strip()]:
        if sid in styles:
            chosen.append(styles[sid])
        else:
            print(f"AVISO: estilo desconhecido ignorado: {sid}", file=sys.stderr)
    return chosen


def write_manifest_entries(output_dir, plan, results):
    """results: dict version -> ok(bool)."""
    manifest = load_manifest(output_dir)
    now = datetime.now(timezone.utc).isoformat()
    for j in plan["jobs"]:
        ctx = j.get("ctx", plan["context"])
        ok = results.get(j["version"], os.path.isfile(j["out_path"]))
        manifest["entries"].append({
            "version": j["version"], "style": j["style"], "style_name": j["style_name"],
            "out_path": j["out_path"], "input_image": plan["input_image"],
            "parent_version": plan.get("parent_version"),
            "resolution": plan["resolution"], "aspect_ratio": plan["aspect_ratio"],
            "provider": plan.get("provider"), "model": plan.get("model"),
            "room": ctx.get("room"), "objective": ctx.get("objective"),
            "tonality": ctx.get("tonality"), "palette": ctx.get("palette"),
            "density": ctx.get("density"), "level": ctx.get("level"), "notes": ctx.get("notes"),
            "prompt": j["prompt"], "ok": ok, "created_at": now,
        })
    save_manifest(output_dir, manifest)


def make_plan(args, styles):
    os.makedirs(args.output_dir, exist_ok=True)
    manifest = load_manifest(args.output_dir)

    input_image = args.input_image
    parent_version = None
    if args.base_version is not None:
        match = [e for e in manifest["entries"] if e["version"] == args.base_version]
        if not match:
            print(f"ERRO: versão base {args.base_version} não encontrada.", file=sys.stderr)
            sys.exit(1)
        input_image = match[-1]["out_path"]
        parent_version = args.base_version

    if not input_image or not os.path.isfile(input_image):
        print(f"ERRO: imagem de entrada não encontrada: {input_image}", file=sys.stderr)
        sys.exit(1)

    aspect = args.aspect_ratio or detect_aspect_ratio(input_image)
    base_ctx = {"room": args.room, "objective": args.objective, "tonality": args.tonality,
                "palette": args.palette, "density": args.density, "level": args.level,
                "notes": args.notes}

    dest_dir = os.path.join(args.output_dir, args.subdir) if args.subdir else args.output_dir
    if args.combos:
        raw = args.combos.strip()
        combos = json.loads(raw) if raw.startswith("[") else json.load(open(raw, encoding="utf-8"))
        jobs = build_jobs_from_combos(dest_dir, combos, base_ctx, styles, next_version(manifest))
    else:
        chosen = resolve_styles(styles, args.styles or "")
        if not chosen:
            print("ERRO: nenhum estilo válido selecionado.", file=sys.stderr)
            sys.exit(1)
        jobs = build_jobs(dest_dir, chosen, base_ctx, next_version(manifest))

    if not jobs:
        print("ERRO: nenhum job válido para gerar.", file=sys.stderr)
        sys.exit(1)
    ctx = base_ctx
    model = args.model or DEFAULT_MODEL[args.provider]
    return {
        "input_image": input_image, "output_dir": args.output_dir, "aspect_ratio": aspect,
        "resolution": args.resolution, "provider": args.provider, "model": model,
        "parent_version": parent_version, "context": ctx, "jobs": jobs,
    }


def main():
    p = argparse.ArgumentParser(description="Orquestrador de redecoração (Nano Banana Pro)")
    p.add_argument("--input-image")
    p.add_argument("--output-dir", default="./redecoracao", help="Raiz do PROJETO (manifest fica aqui)")
    p.add_argument("--subdir", default="exploracao",
                   help="Subpasta da fase para salvar as imagens (ex.: exploracao, edicoes, produtos)")
    p.add_argument("--styles", help="IDs separados por vírgula (ou 'all')")
    p.add_argument("--combos", help="JSON (string ou arquivo) com 1 combo por job: "
                                    "[{\"style\":id,\"level\":..,\"tonality\":..,\"palette\":..,\"notes\":..}]. "
                                    "Permite variar estilo/nível/tonalidade por imagem. Tem prioridade sobre --styles.")
    p.add_argument("--resolution", default="1K", choices=["1K", "2K", "4K"])
    p.add_argument("--aspect-ratio", default=None, help="Default: detecta da foto")
    p.add_argument("--room", default=None)
    p.add_argument("--objective", default=None)
    p.add_argument("--tonality", default=None, choices=["claro", "medio", "escuro"])
    p.add_argument("--palette", default=None)
    p.add_argument("--density", default=None, choices=["minimalista", "equilibrada", "completo"])
    p.add_argument("--level", default="leve", choices=["leve", "medio", "agressivo", "reforma"],
                   help="Nível de complexidade (reforma = permite obra estrutural solicitada)")
    p.add_argument("--notes", default=None)
    p.add_argument("--provider", default=DEFAULT_PROVIDER, choices=["openrouter", "gemini"],
                   help=f"Backend (default: {DEFAULT_PROVIDER})")
    p.add_argument("--model", default=None,
                   help="ID do modelo (default depende do provider; Pro por padrão)")
    p.add_argument("--base-version", type=int, default=None)
    # Modos especiais (subagents):
    p.add_argument("--plan", action="store_true", help="Planeja e grava _plan.json (não gera)")
    p.add_argument("--run-job", nargs=2, metavar=("PLAN", "INDEX"), help="Executa 1 job do plano")
    p.add_argument("--finalize", metavar="PLAN", help="Registra no manifest os jobs do plano")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    styles = load_styles()

    if args.list:
        by_cat = {}
        for s in styles.values():
            by_cat.setdefault(s["category"], []).append(s)
        for cat in sorted(by_cat):
            print(f"\n## {cat}")
            for s in by_cat[cat]:
                print(f"  - {s['id']:<20} {s['name']} [{s['tonality']}]")
        return

    # --- Modo: executar um job (subagent) ---
    if args.run_job:
        plan_path, idx = args.run_job[0], int(args.run_job[1])
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        j = plan["jobs"][idx]
        provider = plan.get("provider", DEFAULT_PROVIDER)
        ok, out, err = generate_one(plan["input_image"], j["out_path"], plan["resolution"],
                                    plan["aspect_ratio"], j["prompt"],
                                    plan.get("model") or DEFAULT_MODEL[provider], provider)
        print(f"[{'OK' if ok else 'FALHOU'}] v{j['version']:02d}-{j['style']} -> {j['out_path']}")
        if not ok:
            print(err or out, file=sys.stderr)
            sys.exit(1)
        return

    # --- Modo: finalizar (registrar manifest) ---
    if args.finalize:
        with open(args.finalize, "r", encoding="utf-8") as f:
            plan = json.load(f)
        write_manifest_entries(plan["output_dir"], plan, results={})
        ok = sum(1 for j in plan["jobs"] if os.path.isfile(j["out_path"]))
        print(f"Manifest atualizado: {ok}/{len(plan['jobs'])} imagens registradas em {plan['output_dir']}")
        return

    # --- Modo: planejar (subagents) ---
    if args.plan:
        plan = make_plan(args, styles)
        plan_file = os.path.join(args.output_dir, "_plan.json")
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"Plano criado: {plan_file}")
        print(f"Proporção travada: {plan['aspect_ratio']} | Resolução: {plan['resolution']} | Jobs: {len(plan['jobs'])}")
        for i, j in enumerate(plan["jobs"]):
            print(f"  [{i}] v{j['version']:02d}-{j['style']} -> {j['out_path']}")
        return

    # --- Modo: direto (gera tudo em paralelo por threads) ---
    plan = make_plan(args, styles)
    print(f"Proporção travada: {plan['aspect_ratio']} | Resolução: {plan['resolution']} | Estilos: {len(plan['jobs'])}")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(plan["jobs"]))) as ex:
        futs = {
            ex.submit(generate_one, plan["input_image"], j["out_path"], plan["resolution"],
                      plan["aspect_ratio"], j["prompt"], plan["model"], plan["provider"]): j
            for j in plan["jobs"]
        }
        for fut in concurrent.futures.as_completed(futs):
            j = futs[fut]
            ok, out, err = fut.result()
            results[j["version"]] = ok
            print(f"  [{'OK' if ok else 'FALHOU'}] v{j['version']:02d}-{j['style']}")
            if not ok:
                print(f"        {err or out}", file=sys.stderr)
    write_manifest_entries(plan["output_dir"], plan, results)
    ok_count = sum(1 for v in results.values() if v)
    print(f"\nConcluído: {ok_count}/{len(plan['jobs'])} imagens em {plan['output_dir']}")


if __name__ == "__main__":
    main()
