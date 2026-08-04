"""Banc d'essai multi-modèles : quel LLM local fera vivre Aletheia.

Banc isolé — HTTP direct vers llama.cpp, ni NATS, ni cortex, ni hippocampe
(ADR 0002 : on reste sur llama.cpp local). Pour chaque modèle du fichier de
config : spawn de llama-server, attente de /health, scénarios, arrêt, suivant.

    python -m eval.main [config.json]      # depuis services/lobe_frontal/

Le tableau final donne des chiffres bruts, pas de score composite : pondérer le
TTFF contre la fiabilité des outils, c'est la décision produit elle-même.
"""

import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from rich.console import Console
from rich.table import Table

from eval.fixtures import (SCENARIO_ORDER, build_messages, config_hashes,
                           load_scenarios, typique_context)
from eval.quality import CONTROLS, blind_sheet, disqualifications, rates
from eval.stream import consume_stream
from src.prompt_builder import PromptBuilder

load_dotenv()

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVAL_DIR, "results")
DEFAULT_CONFIG = os.path.join(EVAL_DIR, "models.json")

# Réglages d'échantillonnage de production : un modèle doit être mesuré tel
# qu'il tournera (cf. .env de lobe_frontal), pas dans un mode greedy flatteur.
TEMPERATURE = float(os.getenv("TEMPERATURE", 0.9))
TOP_P = float(os.getenv("TOP_P", 0.95))
# Mêmes défauts que main.py : un fallback qui diverge ferait mesurer un réglage
# que la production n'utilise pas.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "default")
ENABLE_THINKING = str(REASONING_EFFORT).strip().lower() != "none"

BOOT_TIMEOUT = 300.0
console = Console()


def port_from_argv(argv: list[str]) -> int:
    """L'argv est la seule source de vérité de ce qui tourne : on en extrait le
    port plutôt que de le redéclarer à côté, où il pourrait diverger."""
    # ponytail: `--port` seulement — `-p` est `--prompt` côté llama-server.
    if "--port" in argv:
        return int(argv[argv.index("--port") + 1])
    return 8080


async def wait_for_health(port: int, proc: subprocess.Popen, deadline: float) -> None:
    url = f"http://127.0.0.1:{port}/health"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"llama-server s'est arrêté (code {proc.returncode}) avant d'être prêt")
            try:
                if (await client.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"llama-server n'a pas répondu sur {url} en {BOOT_TIMEOUT:.0f}s")


async def port_is_busy(port: int) -> bool:
    async with httpx.AsyncClient(timeout=1.0) as client:
        try:
            return (await client.get(f"http://127.0.0.1:{port}/health")).status_code == 200
        except httpx.HTTPError:
            return False


def sampling_settings(cfg: dict) -> dict:
    """Tout ce qui influe sur ce qui a réellement tourné, recopié dans le rapport :
    l'argv seul ne suffit pas, ces réglages-là viennent du .env de lobe_frontal."""
    return {
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "reasoning_effort": REASONING_EFFORT,
        "enable_thinking": ENABLE_THINKING,
        "seed": cfg.get("seed", 1337),
        "max_tokens": cfg.get("max_tokens", 200),
        "cache_prompt": {"vitesse": False, "qualite": True},
        # Le seed varie sur le groupe `qualité` : fixe, il rejouerait cinq fois
        # la même réponse et le taux ne mesurerait rien.
        "quality_seeds": list(range(1, cfg.get("quality_runs", 5) + 1)),
    }


async def one_run(client: AsyncOpenAI, model: str, messages: list[dict], tools: list[dict],
                  cfg: dict, seed: int, cache_prompt: bool = False) -> dict:
    start = time.monotonic()
    stream = await client.chat.completions.create(
        messages=messages,
        model=model,
        tools=tools,
        stream=True,
        stream_options={"include_usage": True},
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=cfg.get("max_tokens", 200),
        seed=seed,
        reasoning_effort=REASONING_EFFORT,
        extra_body={
            # false sur les scénarios de vitesse : sinon llama.cpp réutilise le
            # préfixe KV du run précédent et on mesure son cache, pas le modèle.
            # true sur le groupe `qualité`, qui ne chronomètre rien et dont les
            # 6 prompts partagent le même préfixe — quelques dizaines de secondes
            # par modèle au lieu de plusieurs minutes.
            "cache_prompt": cache_prompt,
            "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
        },
    )
    return await consume_stream(stream, start)


async def run_quality_group(client: AsyncOpenAI, model: str, cfg: dict, pb: PromptBuilder,
                            tools: list[dict], ctx: tuple[list[dict], str]) -> list[dict]:
    """Les 6 contrôles, 5 runs chacun, montés sur le contexte de `typique`.

    Seed variable et échantillonnage de production : c'est la fiabilité sous
    stochasticité réelle qu'on mesure, pas une réponse chanceuse rejouée.
    """
    history, rag_results = ctx
    runs = []
    console.print("  · groupe [magenta]qualité[/magenta] :", end="")
    for control in CONTROLS:
        messages = build_messages(pb, control["prompt"], history, rag_results)
        for seed in range(1, cfg.get("quality_runs", 5) + 1):
            result = await one_run(client, model, messages, tools, cfg, seed, cache_prompt=True)
            runs.append({"model": model, "control": control["id"], "seed": seed, **result})
        console.print(f" {control['id']}", end="")
    console.print()
    return runs


async def bench_model(entry: dict, cfg: dict, pb: PromptBuilder, scenarios: dict,
                      quality_ctx: tuple | None) -> tuple[list[dict], list[dict], list[dict]]:
    """Retourne (runs, quality_runs, failures). Un candidat qui casse ne tue
    jamais le banc, et un scénario qui casse n'invalide pas les précédents."""
    name, argv = entry["name"], entry["argv"]
    port = port_from_argv(argv)
    runs, quality_runs, failures, proc, client = [], [], [], None, None
    log = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    def log_tail() -> str:
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size - 8000))
        return "".join(log.readlines()[-40:])

    try:
        if await port_is_busy(port):
            raise RuntimeError(f"le port {port} répond déjà — arrête ton llama-server avant de lancer le banc")

        console.print(f"[bold]▶ {name}[/bold] — démarrage (port {port})…")
        # stdout+stderr vers un fichier : llama.cpp est volubile, un PIPE non lu
        # finirait par bloquer le serveur au milieu d'une mesure.
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
        await wait_for_health(port, proc, time.monotonic() + BOOT_TIMEOUT)

        client = AsyncOpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-vide",
                             http_client=httpx.AsyncClient(timeout=120.0))
        tools = pb.tools_schema
        seed = cfg.get("seed", 1337)

        for scenario in [s for s in SCENARIO_ORDER if s in scenarios]:
            build = scenarios[scenario]
            console.print(f"  · scénario [cyan]{scenario}[/cyan] : chauffe…", end="")
            try:
                # Run de chauffe jeté : il paie le premier accès disque/VRAM.
                await one_run(client, name, build(pb), tools, cfg, seed)
                for i in range(cfg.get("runs", 5)):
                    result = await one_run(client, name, build(pb), tools, cfg, seed)
                    runs.append({"model": name, "argv": argv, "scenario": scenario, "run": i, **result})
                    ttff = result["ttff"]
                    console.print(" " + (f"{ttff * 1000:.0f}ms" if ttff else "—"), end="")
                console.print()
            except Exception as e:
                # Typiquement : n_ctx trop petit pour `chargé`. Les scénarios
                # déjà mesurés restent valides, c'est tout l'intérêt.
                console.print(f"\n[red]  ✗ {scenario} : {e}[/red]")
                failures.append({"model": name, "argv": argv, "scenario": scenario,
                                 "error": str(e), "server_log_tail": log_tail()})
                if proc.poll() is not None:
                    failures[-1]["error"] += " (serveur mort, scénarios suivants abandonnés)"
                    return runs, quality_runs, failures

        if quality_ctx and proc.poll() is None:
            try:
                quality_runs.extend(
                    await run_quality_group(client, name, cfg, pb, tools, quality_ctx))
            except Exception as e:
                console.print(f"\n[red]  ✗ qualité : {e}[/red]")
                failures.append({"model": name, "argv": argv, "scenario": "qualité",
                                 "error": str(e), "server_log_tail": log_tail()})
        return runs, quality_runs, failures

    except Exception as e:
        console.print(f"[red]✗ {name} : {e}[/red]")
        failures.append({"model": name, "argv": argv, "scenario": None,
                         "error": str(e), "server_log_tail": log_tail()})
        return runs, quality_runs, failures

    finally:
        if client:
            await client.close()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.close()


def stats(values: list) -> dict | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}


def fmt(s: dict | None, scale: float = 1.0, digits: int = 0) -> str:
    if not s:
        return "—"
    return (f"{s['median'] * scale:.{digits}f} "
            f"[dim]({s['min'] * scale:.{digits}f}–{s['max'] * scale:.{digits}f})[/dim]")


def render(runs: list[dict], failures: list[dict]) -> None:
    table = Table(title="Banc modèles — médiane (min–max) sur les runs mesurés")
    # « Runs » n'est pas décoratif : un modèle qui meurt au 3e run laisse quand
    # même ses 2 premiers dans le tableau, et une médiane sur 2 runs ne se lit
    # pas comme une médiane sur 5.
    for col in ("Modèle", "Scénario", "Runs", "TTFT ms", "TTFF ms", "Décodage tok/s", "Prefill tok/s", "Tokens entrée"):
        table.add_column(col, justify="left" if col in ("Modèle", "Scénario") else "right")

    # Groupé par modèle, scénarios dans l'ordre froid → typique → chargé : c'est
    # la dégradation d'un modèle d'un scénario à l'autre qu'on vient lire, pas
    # la valeur absolue d'une case.
    models = list(dict.fromkeys(r["model"] for r in runs))
    for model in models:
        for scenario in SCENARIO_ORDER:
            group = [x for x in runs if x["model"] == model and x["scenario"] == scenario]
            if not group:
                continue
            table.add_row(
                model, scenario, str(len(group)),
                fmt(stats([x["ttft"] for x in group]), 1000),
                fmt(stats([x["ttff"] for x in group]), 1000),
                fmt(stats([x["decode_tps"] for x in group]), digits=1),
                fmt(stats([x["prefill_tps"] for x in group]), digits=0),
                fmt(stats([x["prompt_tokens"] for x in group])),
            )
    console.print(table)

    # L'argv sous le tableau, pas dedans : deux modèles mesurés avec des flags
    # différents ne sont pas comparables, et la ligne fait 100 caractères.
    for model, argv in {r["model"]: r["argv"] for r in runs}.items():
        console.print(f"[dim]{model} :[/dim] {' '.join(argv)}")

    for f in failures:
        # Pas de crochets autour du scénario : rich les lirait comme une balise
        # de style et les avalerait silencieusement.
        scope = f.get("scenario") or "démarrage"
        console.print(f"[red]✗ {f['model']} · {scope}[/red] : {f['error']}")
    console.print("[dim]Pas de score composite : arbitrer TTFF contre fiabilité, c'est ta décision.[/dim]")


def render_quality(quality_runs: list[dict], speed_runs: list[dict]) -> None:
    if not quality_runs:
        return
    table = Table(title="Groupe qualité — taux de réussite sur 5 runs, seed variable")
    table.add_column("Modèle")
    for control in CONTROLS:
        if control["check"] is not None:
            table.add_column(control["id"], justify="right")
    table.add_column("Éliminatoire", style="red")

    for model in dict.fromkeys(r["model"] for r in quality_runs):
        model_runs = [r for r in quality_runs if r["model"] == model]
        model_rates = rates(model_runs)
        cells = []
        for control in CONTROLS:
            if control["check"] is None:
                continue
            ok, total = model_rates.get(control["id"], (0, 0))
            # Un taux partiel se lit à l'œil : plein en vert, nul en rouge, le
            # reste en jaune — c'est l'erratique qu'on cherche à repérer.
            colour = "green" if total and ok == total else ("red" if ok == 0 else "yellow")
            cells.append(f"[{colour}]{ok}/{total}[/{colour}]" if total else "—")
        # Les runs de vitesse comptent aussi pour les drapeaux : une fuite du
        # prompt système y est aussi disqualifiante qu'ailleurs.
        flags = disqualifications(model_runs + [r for r in speed_runs if r["model"] == model])
        table.add_row(model, *cells, ", ".join(flags) if flags else "")
    console.print(table)
    console.print("[dim]`piege` n'a pas de vérification automatique : il part en notation aveugle.[/dim]")


async def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    pb = PromptBuilder()
    scenarios, fixture_meta = load_scenarios()
    if fixture_meta is None:
        console.print("[yellow]⚠ Pas de fixtures d'historique — seul `froid` sera mesuré. "
                      "Lance `python eval/dump_history.py` (Postgres allumé) pour les générer.[/yellow]")
    elif fixture_meta.get("stale_prompt"):
        console.print("[yellow]⚠ Les fixtures ont été dumpées avec un autre message final : "
                      "leur <recall> ne correspond plus. Redumpe-les.[/yellow]")

    # Le groupe `qualité` se monte sur le contexte de `typique` : sans fixtures,
    # il n'a pas de conditions réelles où jouer, donc il ne joue pas.
    quality_ctx = typique_context()
    if quality_ctx is None and fixture_meta is not None:
        console.print("[yellow]⚠ Fixture sans scénario `typique` : groupe qualité ignoré.[/yellow]")

    runs, quality_runs, failures = [], [], []
    for entry in cfg["models"]:
        model_runs, model_quality, model_failures = await bench_model(
            entry, cfg, pb, scenarios, quality_ctx)
        runs.extend(model_runs)
        quality_runs.extend(model_quality)
        failures.extend(model_failures)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
    out = os.path.join(RESULTS_DIR, f"{stamp}.json")

    sheet, blind_labels = blind_sheet(quality_runs) if quality_runs else ("", {})
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "config": cfg,
            "sampling": sampling_settings(cfg),
            # Persona/mémoire/utilisateurs se recompilent dans le prompt système à
            # chaque run : sans leur empreinte, deux campagnes à des semaines
            # d'écart se compareraient comme si elles avaient mesuré la même chose.
            "config_hashes": config_hashes(),
            "fixtures": fixture_meta,
            "runs": runs,
            "quality_runs": quality_runs,
            # La correspondance vit ici et pas dans la feuille : elle doit être
            # récupérable après notation, pas pendant.
            "blind_labels": blind_labels,
            "failures": failures,
        }, f, ensure_ascii=False, indent=2)

    render(runs, failures)
    render_quality(quality_runs, runs)
    console.print(f"[dim]→ {out}[/dim]")

    if sheet:
        blind_out = os.path.join(RESULTS_DIR, f"{stamp}-blind.md")
        with open(blind_out, "w", encoding="utf-8") as f:
            f.write(sheet)
        console.print(f"[dim]→ {blind_out}  (à noter avant d'ouvrir le JSON)[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
