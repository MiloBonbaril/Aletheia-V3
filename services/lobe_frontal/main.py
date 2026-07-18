import os
import re
import json
import nats
import time
import asyncio
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    # ponytail: fichier plutôt que stdout/stderr pour ne pas corrompre le rendu
    # Textual du TUI. Le routage WARNING+ vers le TUI (toast + barre de statut)
    # se fait via TUIAlertHandler, attaché après la création de tui_app.
    handlers=[logging.FileHandler(os.path.join(os.path.dirname(__file__), "lobe_frontal.log"))]
)
logger = logging.getLogger("LobeFrontal")

from dotenv import load_dotenv
from OpenAI.interface import OpenAIInterface
from src.prompt_builder import PromptBuilder
from tui import LobeTUI

load_dotenv()

nc = None
PUNCTUATION_PATTERN = re.compile(r'([.!?\n]+)')
INFERENCE_SEMAPHORE = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_INFERENCE", "1")))
MAX_TOOL_ITERATIONS = 10

# Configuration LLM forcée sur notre interface optimisée
interface = OpenAIInterface()
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
TEMPERATURE = float(os.getenv("TEMPERATURE", 0.9))
TOP_P = float(os.getenv("TOP_P", 0.95))
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "default")

prompt_builder = PromptBuilder()
tui_app = LobeTUI()
# ponytail : pas de fetch de modèle ici (l'ancien MODEL_DETAILS = asyncio.run(...) faisait
# double emploi avec refresh_inference_connection(), appelée juste après dans main() —
# get_models()/get_model_details() ne sont donc appelés qu'une fois, au bon endroit, non-fatal.
tui_app.current_effort = REASONING_EFFORT

class TUIAlertHandler(logging.Handler):
    """Fait remonter WARNING+ (tous modules, via propagation sur 'LobeFrontal') vers le
    toast + la barre de statut du TUI ; le FileHandler racine reste seul responsable du fichier."""
    def emit(self, record: logging.LogRecord) -> None:
        severity = "error" if record.levelno >= logging.ERROR else "warning"
        tui_app.alert(self.format(record), severity)

logger.addHandler(TUIAlertHandler(level=logging.WARNING))

async def refresh_inference_connection():
    """Ré-exécute les mêmes appels de découverte de modèle qu'au démarrage (get_models +
    get_model_details), sans reconstruire le client OpenAI — pour un cold start où
    llama.cpp n'était pas encore prêt, ou un redémarrage de llama.cpp en cours de session
    (voir docs/adr/0001-embedded-tui-in-lobe-frontal.md). Câblée sur tui_app.on_reconnect
    ('r') et appelée une première fois dans main().
    Seul get_models() a besoin d'un try/except : get_model_details() avale déjà ses
    propres erreurs I/O en interne (voir OpenAI/interface.py) et ne lève jamais."""
    try:
        models = await interface.get_models()
        logger.info(f"✅ Connecté au serveur d'inférence! Modèles disponibles: {models}")
    except Exception as e:
        logger.error(f"Erreur lors de la connexion au serveur d'inférence: {e}")
        tui_app.set_disconnected(str(e))
        return
    model_details = await interface.get_model_details(MODEL)
    tui_app.set_effort_options(model_details["supported_reasoning_efforts"], tui_app.current_effort)

tui_app.on_reconnect = refresh_inference_connection

async def publish_fragment(sequence: int, text: str, is_last: bool, turn_start: float | None = None):
    """Point de passage unique pour lobe.fragment_stream, pour que le panneau de sortie reflète exactement ce qui est publié.
    turn_start (optionnel) permet d'afficher le délai jusqu'au premier fragment du tour."""
    await nc.publish("lobe.fragment_stream", json.dumps({
        "sequence": sequence, "text": text, "is_last": is_last
    }).encode())
    if sequence == 0 and turn_start is not None:
        tui_app.append_output(f"⏱ premier fragment à {time.monotonic() - turn_start:.3f}s\n")
    tui_app.append_output(text)

async def save_to_memory(text: str):
    try:
        response = await nc.request("hippocampe.rag.add", json.dumps({"content": text}).encode(), timeout=5.0)
        return json.loads(response.data.decode())["result"]
    except Exception as e:
        logger.error(f"Erreur save_to_memory: {e}")
        return ""

async def get_from_memory(prompt: str):
    try:
        response = await nc.request("hippocampe.rag.query", json.dumps({"prompt": prompt}).encode(), timeout=5.0)
        return json.loads(response.data.decode())["result"]
    except Exception as e:
        logger.error(f"Erreur get_from_memory: {e}")
        return ""

available_functions = {
    "save_to_memory": save_to_memory,
    "get_from_memory": get_from_memory
}

async def execute_tool_calls(tool_calls: list, sequence, turn_start: float | None = None) -> list[dict] | None:
    """Exécute tous les appels d'outils en PARALLÈLE pour maximiser le débit.
    Affiche aussi nom/arguments/résultat de chaque outil dans le panneau de sortie du TUI."""

    async def run_single_tool(tc):
        # Unification du format (dict vs objet natif)
        if isinstance(tc, dict):
            f_name = tc.get("function", {}).get("name")
            args_str = tc.get("function", {}).get("arguments", "{}")
            tc_id = tc.get("id")
        else:
            f_name = tc.function.name
            args_str = tc.function.arguments
            tc_id = tc.id

        if f_name == "stay_silent":
            return "SILENT", tc_id, f_name, args_str, None

        try:
            f_args = json.loads(args_str) if args_str else {}
        except Exception:
            f_args = {}

        if f_name not in available_functions:
            return "ERROR", tc_id, f_name, args_str, f"Unknown tool: {f_name}"

        logger.info(f"🚀 Lancement de l'outil en arrière-plan: {f_name}")
        res = await available_functions[f_name](**f_args)
        return "OK", tc_id, f_name, args_str, res

    # Lancement simultané de toutes les tâches d'outils
    tasks = [run_single_tool(tc) for tc in tool_calls]
    completed_tasks = await asyncio.gather(*tasks)

    results = []
    for status, tc_id, name, args_str, content in completed_tasks:
        display_result = content if status != "SILENT" else "(silence)"
        tui_app.append_output(f"\n🔧 outil {name}({args_str}) → {display_result}\n")

        if status == "SILENT":
            logger.info("🤫 Silence radio activé.")
            await publish_fragment(sequence, "", True, turn_start)
            return None

        results.append({
            "tool_call_id": tc_id,
            "role": "tool",
            "name": name,
            "content": content,
        })
    return results


async def main():
    global nc
    logger.info("🧠 En attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        logger.error(f"Échec NATS: {e}")
        return
    logger.info("✅ Connecté à NATS.")
    logger.info("Connection au serveur d'inférence...")
    # Non-fatal : un échec ici (llama.cpp pas encore prêt) ne tue plus le process, la TUI
    # démarre quand même en état déconnecté visible (cf. ticket #8) ; 'r' relance ce même appel.
    await refresh_inference_connection()

    # ── Attente passive du contexte hippocampe ──
    pending_contexts: dict[str, asyncio.Future] = {}

    async def context_ready_handler(msg):
        """Reçoit le contexte pré-calculé par l'hippocampe."""
        try:
            data = json.loads(msg.data.decode())
            corr_id = data.get("correlation_id", "")
            if corr_id in pending_contexts and not pending_contexts[corr_id].done():
                pending_contexts[corr_id].set_result(data)
            else:
                logger.warning(f"⚠️ Contexte reçu pour correlation_id inconnu ou expiré: {corr_id[:8]}...")
        except Exception as e:
            logger.error(f"Erreur parsing context.ready: {e}")

    await nc.subscribe("hippocampe.context.ready", cb=context_ready_handler)
    logger.info("👂 Lobe Frontal abonné à hippocampe.context.ready")

    async def prompt_handler(msg):
        turn_start = time.monotonic()
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        images = data.get("images", [])
        audio = data.get("audio", None)
        correlation_id = data.get("correlation_id", "")

        # Créer un Future pour attendre le contexte de l'hippocampe
        loop = asyncio.get_event_loop()
        context_future = loop.create_future()
        pending_contexts[correlation_id] = context_future

        try:
            # Attendre le contexte avec timeout
            context_data = await asyncio.wait_for(context_future, timeout=0.5)
            history = context_data.get("history", [])
            rag_results = context_data.get("rag_results", "")
            context_summary = context_data.get("context_summary") or None
        except asyncio.TimeoutError:
            logger.error(f"⏰ Timeout contexte hippocampe (corr={correlation_id[:8]}...), annulation de la génération.")
            await publish_fragment(0, "Il semblerait que j'ai des trous de mémoire...", True, turn_start)
            return
        except Exception as e:
            logger.error(f"Erreur attente contexte: {e}")
            await publish_fragment(0, "Il semblerait que j'ai des trous de mémoire...", True, turn_start)
            return
        finally:
            pending_contexts.pop(correlation_id, None)

        messages = prompt_builder.build(prompt, images, audio, history, context_summary, rag_results)
        # Différé via call_soon (même thread/boucle) : le JSON dump + TextArea.load_text
        # peuvent être coûteux avec des images/audio en base64, et ne doivent pas
        # retarder l'appel à get_stream() qui suit (chemin critique du TTFT).
        loop.call_soon(tui_app.update_messages, messages)

        # Log asynchrone du prompt utilisateur en tâche de fond
        asyncio.create_task(nc.publish(
            "hippocampe.history.add",
            json.dumps({"role": "user", "content": prompt_builder.build_user_content_for_db(prompt, images, audio)}).encode()
        ))

        async with INFERENCE_SEMAPHORE:
            sequence = 0
            overall_response_fragments = []
            turn_count = 0

            while turn_count < MAX_TOOL_ITERATIONS:
                turn_count += 1
                try:
                    stream = await interface.get_stream(messages, MODEL, prompt_builder.tools_schema, TEMPERATURE, TOP_P, tui_app.current_effort)
                    
                    tool_calls_dict = {}
                    collected_content = ""
                    finish_reason = None
                    
                    # Buffer glissant l'évaluation de ponctuation en temps réel ($O(1)$)
                    text_buffer = ""

                    async for chunk in stream:
                        if not chunk.choices: continue
                        delta = chunk.choices[0].delta
                        
                        # Accumulation textuelle
                        token = getattr(delta, "content", None)
                        if token:
                            collected_content += token
                            overall_response_fragments.append(token)
                            text_buffer += token

                            # Analyse instantanée sur le buffer glissant
                            match = PUNCTUATION_PATTERN.search(text_buffer)
                            if match:
                                end_idx = match.end()
                                fragment = text_buffer[:end_idx].strip()
                                text_buffer = text_buffer[end_idx:] # On purge uniquement ce qui est envoyé
                                
                                if fragment:
                                    await publish_fragment(sequence, fragment, False, turn_start)
                                    sequence += 1

                        # Agrégation des chunks d'outils (Streaming de l'appel d'outil)
                        if getattr(delta, "tool_calls", None):
                            for tc_chunk in delta.tool_calls:
                                idx = tc_chunk.index
                                if idx not in tool_calls_dict:
                                    tool_calls_dict[idx] = {
                                        "id": getattr(tc_chunk, "id", "") or "",
                                        "type": "function",
                                        "function": {
                                            "name": getattr(tc_chunk.function, "name", "") or "",
                                            "args_buffer": [getattr(tc_chunk.function, "arguments", "") or ""]
                                        }
                                    }
                                else:
                                    if getattr(tc_chunk, "id", None): tool_calls_dict[idx]["id"] = tc_chunk.id
                                    if getattr(tc_chunk.function, "name", None): tool_calls_dict[idx]["function"]["name"] += tc_chunk.function.name
                                    if getattr(tc_chunk.function, "arguments", None): tool_calls_dict[idx]["function"]["args_buffer"].append(tc_chunk.function.arguments)

                        if chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason

                    # Clôture du buffer de chaînes d'arguments pour les outils
                    for tc in tool_calls_dict.values():
                        if "args_buffer" in tc["function"]:
                            tc["function"]["arguments"] = "".join(tc["function"]["args_buffer"])
                            del tc["function"]["args_buffer"]
                    
                    tool_calls = list(tool_calls_dict.values())

                    tui_app.append_output(f"\n── Tour {turn_count}/{MAX_TOOL_ITERATIONS} · finish_reason={finish_reason} ──\n")

                    if tool_calls and finish_reason == "tool_calls":
                        messages.append({
                            "role": "assistant",
                            "content": collected_content or None,
                            "tool_calls": tool_calls
                        })

                        results = await execute_tool_calls(tool_calls, sequence, turn_start)
                        if results is None: return # stay silent

                        messages.extend(results)
                        loop.call_soon(tui_app.update_messages, messages)

                        # Historisation asynchrone en tâche de fond pour ne pas bloquer le tour de boucle
                        for r in results:
                            if r["name"] == "get_from_memory":
                                asyncio.create_task(nc.publish("hippocampe.history.add", json.dumps({"role": "tool", "content": r["content"]}).encode()))

                        finish_reason = None
                        continue
                    else:
                        # Flush final du buffer glissant s'il reste du texte
                        final_text = text_buffer.strip()
                        await publish_fragment(sequence, final_text, True, turn_start)
                        break

                except Exception as e:
                    logger.exception(f"Erreur boucle génération: {e}")
                    await publish_fragment(sequence, "Erreur.", True, turn_start)
                    break

            # Enregistrement final asynchrone
            asyncio.create_task(nc.publish(
                "hippocampe.history.add", 
                json.dumps({"role": "assistant", "content": "".join(overall_response_fragments)}).encode()
            ))

    await nc.subscribe("cortex.prompt", queue="lobe_workers", cb=prompt_handler)
    logger.info("👂 Lobe Frontal prêt à foudroyer le TTFT.")
    # Le TUI partage cette même boucle asyncio (docs/adr/0001) : les callbacks NATS
    # ci-dessus continuent de se déclencher normalement pendant que run_async() tourne.
    await tui_app.run_async()

if __name__ == '__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
