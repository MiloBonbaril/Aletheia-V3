#!/usr/bin/env python3
import os
import sys
import json
import time
import uuid
import asyncio
import logging
from datetime import datetime

# Configure logging to stay clean and out of the way of the CLI dashboard
logging.basicConfig(level=logging.ERROR)

from nats.errors import ConnectionClosedError, TimeoutError, NoServersError
import nats
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.box import ROUNDED, DOUBLE

console = Console()

# Global state for transactions
active_transactions = {}
completed_transactions = []

class BenchmarkGraph:
    def __init__(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
            
        self.name = self.data.get("name", "Benchmark Graph")
        self.description = self.data.get("description", "")
        self.ingress_points = self.data.get("ingress_points", [])
        self.steps = self.data.get("steps", [])
        
        # Verify and extract all topics to listen to
        self.topics = set()
        for ingress in self.ingress_points:
            self.topics.add(ingress.get("topic"))
        for step in self.steps:
            for t in step.get("topics", []):
                self.topics.add(t)

    def find_step_for_event(self, topic, payload):
        """Finds which step in the graph matches the topic and payload filters"""
        for step in self.steps:
            if topic in step.get("topics", []):
                # Evaluate filter if present
                filter_str = step.get("filter")
                if filter_str:
                    try:
                        # Safe evaluation context with payload
                        matched = eval(filter_str, {"__builtins__": None}, {"payload": payload})
                        if matched:
                            return step
                    except Exception:
                        # Skip if filter fails to evaluate
                        continue
                else:
                    return step
        return None

def format_duration(seconds):
    if seconds < 0.001:
        return f"{seconds * 1000000:.0f} µs"
    elif seconds < 1.0:
        return f"{seconds * 1000:.1f} ms"
    else:
        return f"{seconds:.2f} s"

def draw_horizontal_bar(console_width, segments):
    """
    Renders a premium multi-colored horizontal timeline bar based on segments.
    segments: list of dicts with {"name": str, "duration": float, "style": str}
    """
    total_duration = sum(s["duration"] for s in segments)
    if total_duration <= 0:
        return Text("No duration data available")
        
    bar_width = max(10, console_width - 30) # Leave room for metadata
    bar = Text()
    
    accumulated_chars = 0
    for idx, s in enumerate(segments):
        ratio = s["duration"] / total_duration
        chars = round(ratio * bar_width)
        if chars == 0 and ratio > 0:
            chars = 1
        
        accumulated_chars += chars
        # Cap to bar_width
        if idx == len(segments) - 1:
            chars = max(0, bar_width - (accumulated_chars - chars))
            
        if chars > 0:
            # Use distinct blocks for the timeline
            bar.append("█" * chars, style=s["style"])
            
    return bar

def render_transaction_report(tx, graph):
    """Generates a stunning detailed report of a completed transaction"""
    console.print("\n")
    title_text = Text(f"📊 REPORT DE BENCHMARK : {graph.name}", style="bold white")
    console.print(Panel(
        Align.center(title_text),
        box=ROUNDED,
        border_style="bold magenta",
        subtitle=f"ID Transaction: {tx['id']} | Début: {datetime.fromtimestamp(tx['start_time']).strftime('%Y-%m-%d %H:%M:%S')}"
    ))

    # Calculate individual segments and total latency depending on graph structure
    step_ids = [step["id"] for step in graph.steps]
    t_start = tx["start_time"]

    if "voice_playback_end" in step_ids:
        # E2E Graph segments
        t_cortex = tx["timestamps"].get("cortex_dispatch", t_start)
        duration_cortex_latency = t_cortex - t_start
        
        t_hippocampe = tx["timestamps"].get("hippocampe_ready", t_cortex)
        duration_hippocampe = t_hippocampe - t_cortex
        
        t_lobe_first = tx["timestamps"].get("lobe_first_fragment", t_hippocampe)
        duration_llm_ttft = t_lobe_first - t_hippocampe
        
        t_voice_start = tx["timestamps"].get("voice_playback_start", t_lobe_first)
        duration_tts_latency = t_voice_start - t_lobe_first
        
        t_voice_end = tx["timestamps"].get("voice_playback_end", t_voice_start)
        duration_playback = t_voice_end - t_voice_start
        
        duration_total = t_voice_end - t_start

        segments = [
            {"name": "Liaison Cortex", "duration": duration_cortex_latency, "style": "bold blue"},
            {"name": "Mémoire Hippocampe", "duration": duration_hippocampe, "style": "bold cyan"},
            {"name": "Inférence LLM (TTFT)", "duration": duration_llm_ttft, "style": "bold magenta"},
            {"name": "Synthèse TTS", "duration": duration_tts_latency, "style": "bold yellow"},
            {"name": "Lecture Audio", "duration": duration_playback, "style": "bold green"}
        ]
    elif "lobe_last_fragment" in step_ids:
        # T2T Graph segments
        t_cortex = tx["timestamps"].get("cortex_dispatch", t_start)
        duration_cortex_latency = t_cortex - t_start
        
        t_hippocampe = tx["timestamps"].get("hippocampe_ready", t_cortex)
        duration_hippocampe = t_hippocampe - t_cortex
        
        t_lobe_first = tx["timestamps"].get("lobe_first_fragment", t_hippocampe)
        duration_llm_ttft = t_lobe_first - t_hippocampe
        
        t_lobe_last = tx["timestamps"].get("lobe_last_fragment", t_lobe_first)
        duration_generation = t_lobe_last - t_lobe_first
        
        duration_total = t_lobe_last - t_start

        segments = [
            {"name": "Liaison Cortex", "duration": duration_cortex_latency, "style": "bold blue"},
            {"name": "Mémoire Hippocampe", "duration": duration_hippocampe, "style": "bold cyan"},
            {"name": "Inférence LLM (TTFT)", "duration": duration_llm_ttft, "style": "bold magenta"},
            {"name": "Génération LLM", "duration": duration_generation, "style": "bold green"}
        ]
    else:
        # Generic fallback: build segments sequentially for all steps present in the graph
        segments = []
        last_t = t_start
        colors = ["bold blue", "bold magenta", "bold yellow", "bold green", "bold cyan", "bold red"]
        
        for idx, step in enumerate(graph.steps):
            if idx == 0:
                continue
            step_id = step["id"]
            t_step = tx["timestamps"].get(step_id, last_t)
            duration = t_step - last_t
            segments.append({
                "name": step["name"],
                "duration": duration,
                "style": colors[(idx - 1) % len(colors)]
            })
            last_t = t_step
            
        duration_total = last_t - t_start

    # Metrics Table
    table = Table(box=ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Étape", style="bold white")
    table.add_column("Topic NATS", style="dim cyan")
    table.add_column("Temps Absolu", style="bold yellow", justify="right")
    table.add_column("Latence Étape", style="bold magenta", justify="right")
    table.add_column("Description / Détails", style="green")

    last_t = t_start
    for step in graph.steps:
        step_id = step["id"]
        step_name = step["name"]
        step_topics = ", ".join(step["topics"])
        
        t_step = tx["timestamps"].get(step_id)
        if t_step is not None:
            abs_time = format_duration(t_step - t_start)
            step_latency = format_duration(t_step - last_t)
            last_t = t_step
            
            # Extract payload details for display
            payload = tx["payloads"].get(step_id, {})
            details = step["description"]
            if step_id == "user_ingress":
                if "audio" in payload:
                    details = f"📥 [Audio RAW] ({len(payload.get('audio', ''))} chars)"
                else:
                    details = f"📥 Msg: '{payload.get('text', '')}'"
            elif step_id == "cortex_dispatch":
                details = f"🧠 Cortex a routé le message vers Lobe Frontal"
            elif step_id == "hippocampe_ready":
                history_count = len(payload.get("history", []))
                rag_results = payload.get("rag_results", "")
                rag_snippet = (rag_results[:60] + "...") if len(rag_results) > 60 else rag_results
                rag_snippet = rag_snippet.replace('\n', ' ')
                details = f"📚 Mémoire prête : {history_count} msgs hist | RAG: {rag_snippet}"
            elif step_id == "lobe_first_fragment":
                details = f"💬 TTFT: '{payload.get('text', '')}'"
            elif step_id == "voice_playback_start":
                details = f"🔊 Kokoro synthétise et commence à jouer"
            elif step_id == "lobe_last_fragment":
                details = f"✅ LLM fin. Sequence: #{payload.get('sequence', 0)}"
            elif step_id == "voice_playback_end":
                details = f"🔇 Lecture terminée"
                
            table.add_row(
                step_name,
                step_topics,
                abs_time,
                step_latency,
                details
            )
        else:
            table.add_row(
                step_name,
                step_topics,
                "[red]Non atteint[/red]",
                "-",
                step["description"]
            )

    console.print(table)

    # Horizontal Timeline Panel
    
    timeline_text = Text("\n📊 Répartition de la latence :\n", style="bold white")
    timeline_bar = draw_horizontal_bar(console.width, segments)
    timeline_text.append(timeline_bar)
    timeline_text.append("\n\n")
    
    # Legend
    for s in segments:
        timeline_text.append(" ■ ", style=s["style"])
        timeline_text.append(f"{s['name']}: ")
        timeline_text.append(f"{format_duration(s['duration'])}", style="bold white")
        timeline_text.append("  ")
        
    timeline_text.append("\n\n⏱️  ")
    timeline_text.append("Latence End-to-End Totale: ", style="bold cyan")
    timeline_text.append(f"{format_duration(duration_total)}", style="bold green size(15)")
    timeline_text.append("\n")

    console.print(Panel(
        timeline_text,
        title="⌛ TIMELINE VISUELLE DE LA LATENCE",
        box=ROUNDED,
        border_style="bold green"
    ))
    console.print("\n")

async def monitor_timeouts(graph):
    """Triggers report for transactions that haven't gotten events for 15 seconds"""
    while True:
        await asyncio.sleep(2)
        now = time.time()
        to_remove = []
        for tx_id, tx in list(active_transactions.items()):
            # If no events for 15 seconds
            last_event_time = max(tx["timestamps"].values()) if tx["timestamps"] else tx["start_time"]
            if now - last_event_time > 15.0:
                console.print(f"\n[bold yellow]⚠️  Timeout de transaction détecté (aucun événement depuis 15s). Génération du rapport partiel...[/bold yellow]")
                # Fill final step timestamp if missing to generate the report properly
                final_step_id = graph.steps[-1]["id"]
                if final_step_id not in tx["timestamps"]:
                    tx["timestamps"][final_step_id] = last_event_time
                render_transaction_report(tx, graph)
                to_remove.append(tx_id)
                
        for tx_id in to_remove:
            if tx_id in active_transactions:
                del active_transactions[tx_id]

async def main():
    # Print custom beautiful Banner
    banner = Panel(
        Text.assemble(
            ("⚡ A L E T H E I A   B E N C H M A R K   E N G I N E ⚡\n", "bold cyan"),
            ("Système Nerveux Continu & Observabilité Temps Réel", "italic dim white")
        ),
        box=DOUBLE,
        border_style="bold cyan",
        padding=(1, 2)
    )
    console.print(banner)

    # 1. Load benchmark graph
    graph_path = os.path.join(os.path.dirname(__file__), "graphs/E2E.json")
    if len(sys.argv) > 1:
        graph_path = sys.argv[1]
        
    if not os.path.exists(graph_path):
        console.print(f"[bold red]❌ Fichier de graphe introuvable : {graph_path}[/bold red]")
        sys.exit(1)
        
    try:
        graph = BenchmarkGraph(graph_path)
        console.print(f"[bold green]✓[/bold green] Graphe chargé avec succès : [bold white]{graph.name}[/bold white]")
        console.print(f"📝 Description: {graph.description}\n")
    except Exception as e:
        console.print(f"[bold red]❌ Erreur de lecture du graphe JSON : {e}[/bold red]")
        sys.exit(1)

    # 2. Connect to NATS
    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
    console.print(f"🔌 Connexion au broker NATS sur {nats_url}...")
    try:
        nc = await nats.connect(nats_url)
        console.print("[bold green]✅ Connecté au bus NATS avec succès ![/bold green]")
    except Exception as e:
        console.print(f"[bold red]💀 Impossible de se connecter à NATS : {e}[/bold red]")
        sys.exit(1)

    # Subscribe to all topics defined in the graph
    console.print("\n📡 [bold white]Souscription active aux événements :[/bold white]")
    
    async def make_handler(topic):
        async def handler(msg):
            try:
                payload = json.loads(msg.data.decode())
            except Exception:
                payload = {"raw_text": msg.data.decode()}
                
            arrival_time = time.time()
            
            # 1. Check if this is an ingress point (starts a new transaction)
            is_ingress = any(ip.get("topic") == topic for ip in graph.ingress_points)
            
            current_tx = None
            
            if is_ingress:
                # Start new transaction
                tx_id = str(uuid.uuid4())
                current_tx = {
                    "id": tx_id,
                    "ingress_topic": topic,
                    "start_time": arrival_time,
                    "timestamps": {},
                    "payloads": {},
                    "completed": False
                }
                active_transactions[tx_id] = current_tx
                console.print(f"\n[bold cyan]🚀 [NOUVEAU FLUX DÉTECTÉ] sur {topic}[/bold cyan]")
            else:
                # Match to the most recent active transaction
                if active_transactions:
                    # Get the newest active transaction
                    newest_id = list(active_transactions.keys())[-1]
                    current_tx = active_transactions[newest_id]
            
            if current_tx:
                # Find step matching this event
                step = graph.find_step_for_event(topic, payload)
                if step:
                    step_id = step["id"]
                    # If this step hasn't been recorded yet in this transaction
                    if step_id not in current_tx["timestamps"]:
                        current_tx["timestamps"][step_id] = arrival_time
                        current_tx["payloads"][step_id] = payload
                        
                        latency = arrival_time - current_tx["start_time"]
                        color = step.get("display_color", "white")
                        console.print(
                            f"  [bold {color}]▶[/bold {color}] [bold]{step['name']}[/bold] "
                            f"({topic}) | Latence absolue: [bold yellow]{format_duration(latency)}[/bold yellow]"
                        )
                        
                        # Check if transaction is completed
                        # The transaction ends when the last step of the graph is reached
                        if step_id == graph.steps[-1]["id"]:
                            current_tx["completed"] = True
                            # Remove from active and draw report
                            tx_id = current_tx["id"]
                            if tx_id in active_transactions:
                                del active_transactions[tx_id]
                            render_transaction_report(current_tx, graph)
        return handler

    for topic in graph.topics:
        try:
            handler = await make_handler(topic)
            await nc.subscribe(topic, cb=handler)
            console.print(f"  [bold green]•[/bold green] [dim]{topic}[/dim]")
        except Exception as e:
            console.print(f"  [bold red]✗ Échec souscription {topic} : {e}[/bold red]")

    console.print("\n[bold green]🎯 Le service de benchmark écoute en continu. Lancez des requêtes pour voir les résultats ![/bold green]")
    console.print("[dim]Appuyez sur Ctrl+C pour arrêter le service.[/dim]\n")

    # Start the timeout monitoring loop
    timeout_task = asyncio.create_task(monitor_timeouts(graph))

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]🛑 Arrêt du service de benchmark...[/bold yellow]")
    finally:
        timeout_task.cancel()
        await nc.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
