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
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "none")

BOOT_TIMEOUT = 300.0
console = Console()

# Message utilisateur du scénario `froid` : aucun historique, aucun <recall>,
# juste le prompt système + une phrase. C'est le plancher absolu de latence.
FROID_PROMPT = "Salut Aletheia, tu fais quoi de beau ?"


def scenario_froid(pb: PromptBuilder) -> list[dict]:
    return pb.build(FROID_PROMPT)


# Construits à la volée par PromptBuilder à chaque run — jamais figés, sinon le
# banc mesurerait un prompt que la production n'envoie plus.
SCENARIOS = {"froid": scenario_froid}


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


async def one_run(client: AsyncOpenAI, model: str, messages: list[dict], tools: list[dict],
                  cfg: dict, cache_prompt: bool, seed: int) -> dict:
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
            # Sans ça, llama.cpp réutilise le préfixe KV du run précédent : on
            # mesurerait son cache, pas le modèle.
            "cache_prompt": cache_prompt,
            "chat_template_kwargs": {"enable_thinking": str(REASONING_EFFORT).strip().lower() != "none"},
        },
    )
    return await consume_stream(stream, start)


async def bench_model(entry: dict, cfg: dict, pb: PromptBuilder) -> tuple[list[dict], dict | None]:
    """Retourne (runs, failure). Un candidat qui casse ne tue jamais le banc."""
    name, argv = entry["name"], entry["argv"]
    port = port_from_argv(argv)
    runs, proc, client = [], None, None
    log = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

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

        for scenario, build in SCENARIOS.items():
            console.print(f"  · scénario [cyan]{scenario}[/cyan] : chauffe…", end="")
            # Run de chauffe jeté : il paie le premier accès disque/VRAM.
            await one_run(client, name, build(pb), tools, cfg, cache_prompt=False, seed=seed)
            for i in range(cfg.get("runs", 5)):
                result = await one_run(client, name, build(pb), tools, cfg, cache_prompt=False, seed=seed)
                runs.append({"model": name, "argv": argv, "scenario": scenario, "run": i, **result})
                ttff = result["ttff"]
                console.print(" " + (f"{ttff * 1000:.0f}ms" if ttff else "—"), end="")
            console.print()
        return runs, None

    except Exception as e:
        log.seek(0)
        tail = "".join(log.readlines()[-40:])
        console.print(f"[red]✗ {name} : {e}[/red]")
        return runs, {"model": name, "argv": argv, "error": str(e), "server_log_tail": tail}

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

    seen = []
    for r in runs:
        key = (r["model"], r["scenario"])
        if key in seen:
            continue
        seen.append(key)
        group = [x for x in runs if (x["model"], x["scenario"]) == key]
        table.add_row(
            r["model"], r["scenario"], str(len(group)),
            fmt(stats([x["ttft"] for x in group]), 1000),
            fmt(stats([x["ttff"] for x in group]), 1000),
            fmt(stats([x["decode_tps"] for x in group]), digits=1),
            fmt(stats([x["prefill_tps"] for x in group]), digits=0),
            fmt(stats([x["prompt_tokens"] for x in group])),
        )
    console.print(table)

    for f in failures:
        console.print(f"[red]✗ {f['model']}[/red] : {f['error']}")
    console.print("[dim]Pas de score composite : arbitrer TTFF contre fiabilité, c'est ta décision.[/dim]")


async def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    pb = PromptBuilder()
    runs, failures = [], []
    for entry in cfg["models"]:
        model_runs, failure = await bench_model(entry, cfg, pb)
        runs.extend(model_runs)
        if failure:
            failures.append(failure)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, f"{datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"config": cfg, "runs": runs, "failures": failures}, f, ensure_ascii=False, indent=2)

    render(runs, failures)
    console.print(f"[dim]→ {out}[/dim]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
