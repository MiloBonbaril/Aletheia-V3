import os
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
from src.fragments import take_fragment
from src.turn_queue import is_for_turn, take_latest
from tui import LobeTUI

load_dotenv()

nc = None
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

async def publish_fragment(sequence: int, text: str, is_last: bool, turn_start: float | None = None, correlation_id: str = ""):
    """Point de passage unique pour lobe.fragment_stream, pour que le panneau de sortie reflète exactement ce qui est publié.
    turn_start (optionnel) permet d'afficher le délai jusqu'au premier fragment du tour.
    correlation_id rattache le fragment à sa requête : io_voix le recopie sur io.voice.speak.*,
    et le benchmark s'en sert pour ne pas mélanger deux requêtes qui se chevauchent."""
    await nc.publish("lobe.fragment_stream", json.dumps({
        "sequence": sequence, "text": text, "is_last": is_last, "correlation_id": correlation_id,
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

async def set_mood(emotion: str, intensity: float, description: str | None = None):
    # Fire-and-forget : limbic est seul responsable d'appliquer/rediffuser l'état (cf. docs/adr).
    try:
        payload = {"emotion": emotion, "intensity": intensity, "description": description}
        await nc.publish("limbic.mood.set", json.dumps(payload).encode())
        return "Mood updated."
    except Exception as e:
        logger.error(f"Erreur set_mood: {e}")
        return ""

available_functions = {
    "save_to_memory": save_to_memory,
    "get_from_memory": get_from_memory,
    "set_mood": set_mood
}

async def execute_tool_calls(tool_calls: list, sequence, turn_start: float | None = None, correlation_id: str = "") -> list[dict] | None:
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
            await publish_fragment(sequence, "", True, turn_start, correlation_id)
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
        nc = await nats.connect("nats://localhost:4222", name="lobe_frontal")
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

    async def mood_update_handler(msg):
        """Met en cache le mood canonique rediffusé par limbic (cf. #10) pour le prochain prompt."""
        try:
            prompt_builder.mood = json.loads(msg.data.decode())
            logger.info(f"🎭 Mood mis à jour: {prompt_builder.mood.get('emotion')} ({prompt_builder.mood.get('intensity')})")
        except Exception as e:
            logger.error(f"Erreur parsing mood.update: {e}")

    await nc.subscribe("limbic.mood.update", cb=mood_update_handler)
    logger.info("👂 Lobe Frontal abonné à limbic.mood.update")

    async def vision_state_handler(msg):
        """Met en cache le dernier état de l'écran publié par io_yeux pour le prochain prompt.
        PromptBuilder l'ignore au-delà de 90 s: pas besoin de l'effacer ici."""
        try:
            prompt_builder.vision = json.loads(msg.data.decode())
        except Exception as e:
            logger.error(f"Erreur parsing vision.state: {e}")

    await nc.subscribe("io.vision.state", cb=vision_state_handler)
    logger.info("👂 Lobe Frontal abonné à io.vision.state")

    async def topic_generate_handler(msg):
        """Réflexion silencieuse à la demande de limbic (#15) : jamais publié sur lobe.fragment_stream.
        Passe par INFERENCE_SEMAPHORE comme prompt_handler : sans ça, une réflexion pourrait tourner
        en concurrence d'un vrai tour utilisateur sur le même llama.cpp mono-slot — la réflexion n'a
        aucune contrainte de latence (#15), donc attendre son tour ici ne coûte rien."""
        try:
            async with INFERENCE_SEMAPHORE:
                topic = await interface.get_completion(prompt_builder.build_topic_prompt(), MODEL, TEMPERATURE, TOP_P)
        except Exception as e:
            logger.error(f"Erreur lobe.topic.generate: {e}")
            topic = ""
        await nc.publish(msg.reply, json.dumps({"topic": topic}).encode())

    await nc.subscribe("lobe.topic.generate", queue="lobe_workers", cb=topic_generate_handler)
    logger.info("👂 Lobe Frontal abonné à lobe.topic.generate")

    # ── File « le dernier message gagne » (docs/audits/io_oreilles-discord-multi-speaker-2026-10-12.md, P1) ──
    # nats-py appelle les callbacks d'un abonnement l'un après l'autre : un `prompt_handler`
    # qui faisait l'inférence bloquait le suivant, dont le `context.ready` arrivait alors
    # avant son future et se perdait. Le handler ne fait donc plus que ranger le message ;
    # un seul worker répond, au dernier arrivé, et écrit les autres en mémoire.
    inbox: list[dict] = []
    inbox_ready = asyncio.Event()
    busy = False                 # vrai du début d'un tour jusqu'à ce qu'Aletheia se taise
    current_turn: dict | None = None

    async def history_add(role: str, content, ack: bool = False):
        """Écrit un message dans l'historique. `ack` attend que l'hippocampe l'ait écrit :
        le worker relit l'historique quelques millisecondes après, il doit y être."""
        payload = json.dumps({"role": role, "content": content}).encode()
        if not ack:
            await nc.publish("hippocampe.history.add", payload)
            return
        try:
            await nc.request("hippocampe.history.add", payload, timeout=0.5)
        except Exception as e:
            logger.warning(f"⚠️ Écriture d'historique non confirmée ({role}): {e}")

    async def rebuild_context(item: dict) -> dict:
        """Redemande le contexte d'un message qui a attendu : celui calculé à son arrivée ne
        contient ni la réponse donnée pendant l'attente, ni les messages sautés.
        ponytail: n_history=20 recopié du cortex (cortex/src/main.rs), seul autre publieur."""
        corr = item["correlation_id"]
        future = asyncio.get_running_loop().create_future()
        pending_contexts[corr] = future
        await nc.publish("hippocampe.context.build", json.dumps({
            "prompt": item.get("prompt", ""), "correlation_id": corr, "n_history": 20,
        }).encode())
        return await asyncio.wait_for(future, timeout=0.5)

    async def get_context(item: dict, rebuild: bool) -> dict | None:
        corr = item["correlation_id"]
        try:
            # Le contexte du cortex d'abord, même périmé : l'attendre garantit qu'il ne
            # remplira pas le future de la reconstruction s'il arrive en retard.
            context = await asyncio.wait_for(item["_context"], timeout=0.5)
            if rebuild:
                context = await rebuild_context(item)
            return context
        except asyncio.TimeoutError:
            logger.error(f"⏰ Timeout contexte hippocampe (corr={corr[:8]}...), annulation de la génération.")
        except Exception as e:
            logger.error(f"Erreur attente contexte: {e}")
        finally:
            pending_contexts.pop(corr, None)
        return None

    async def speech_finished(turn: dict):
        """Attend qu'Aletheia se taise (`io.voice.speak.end` du dernier fragment) : un membre
        qui réagit à cette réponse parle pendant qu'elle parle encore, et le tour suivant
        doit l'entendre.
        ponytail: deux garde-fous fixes. Sans TTS (profil sans-vocal), aucun `.start` n'arrive :
        on libère 1 s après le dernier fragment. Si le lecteur meurt, plafond de 60 s."""
        try:
            await asyncio.wait_for(turn["speaking"].wait(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.info("🔇 Aucune voix pour ce tour : la file repart.")
            return
        try:
            await asyncio.wait_for(turn["silent"].wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("⚠️ Pas de io.voice.speak.end après 60 s : la file repart sans lui.")

    def speech_handler(started: bool):
        async def handler(msg):
            try:
                event = json.loads(msg.data.decode())
            except Exception as e:
                logger.error(f"Erreur parsing io.voice.speak: {e}")
                return
            turn = current_turn
            if turn is None or not is_for_turn(event, turn["correlation_id"]):
                return
            if started:
                turn["speaking"].set()
            elif event.get("is_last"):
                turn["silent"].set()
        return handler

    async def prompt_handler(msg):
        """Range le message et rend la main tout de suite. Le future du contexte existe dès
        l'arrivée : le `context.ready` d'un message qui attend n'est plus perdu."""
        data = json.loads(msg.data.decode())
        data.setdefault("correlation_id", "")
        data["_context"] = pending_contexts[data["correlation_id"]] = asyncio.get_running_loop().create_future()
        data["_stale"] = busy
        inbox.append(data)
        inbox_ready.set()

    async def inference_worker():
        nonlocal busy, current_turn
        while True:
            await inbox_ready.wait()
            inbox_ready.clear()
            batch, inbox[:] = inbox[:], []
            skipped, latest = take_latest(batch)
            busy = True
            try:
                for m in skipped:
                    logger.info(f"📥 Message sauté (corr={m['correlation_id'][:8]}...), écrit en mémoire sans réponse.")
                    await history_add("user", prompt_builder.build_user_content_for_db(
                        m.get("prompt", ""), m.get("images", []), m.get("audio")), ack=True)
                if latest is None:
                    logger.info("💤 Prompt proactif périmé jeté : un tour a eu lieu pendant l'attente.")
                    continue
                turn_start = time.monotonic()
                current_turn = {"correlation_id": latest["correlation_id"],
                                "speaking": asyncio.Event(), "silent": asyncio.Event()}
                context_data = await get_context(latest, rebuild=latest["_stale"] or bool(skipped))
                if context_data is None:
                    await publish_fragment(0, "Il semblerait que j'ai des trous de mémoire...", True, turn_start, latest["correlation_id"])
                else:
                    await run_turn(latest, context_data, turn_start)
                await speech_finished(current_turn)
            except Exception as e:
                logger.exception(f"Erreur du worker d'inférence: {e}")
            finally:
                for m in batch:
                    if pending_contexts.get(m["correlation_id"]) is m["_context"]:
                        pending_contexts.pop(m["correlation_id"], None)
                current_turn = None
                busy = False
                if inbox:
                    inbox_ready.set()

    async def run_turn(data: dict, context_data: dict, turn_start: float):
        prompt = data.get("prompt", "")
        images = data.get("images", [])
        audio = data.get("audio", None)
        correlation_id = data["correlation_id"]
        history = context_data.get("history", [])
        rag_results = context_data.get("rag_results", "")
        context_summary = context_data.get("context_summary") or None
        loop = asyncio.get_running_loop()

        messages = prompt_builder.build(prompt, images, audio, history, context_summary, rag_results)
        # Différé via call_soon (même thread/boucle) : le JSON dump + TextArea.load_text
        # peuvent être coûteux avec des images/audio en base64, et ne doivent pas
        # retarder l'appel à get_stream() qui suit (chemin critique du TTFT).
        loop.call_soon(tui_app.update_messages, messages)

        # Log asynchrone du prompt utilisateur en tâche de fond
        asyncio.create_task(history_add("user", prompt_builder.build_user_content_for_db(prompt, images, audio)))

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

                            # Analyse instantanée sur le buffer glissant (on ne
                            # purge que ce qui est envoyé)
                            fragment, text_buffer = take_fragment(text_buffer)
                            if fragment:
                                await publish_fragment(sequence, fragment, False, turn_start, correlation_id)
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

                        results = await execute_tool_calls(tool_calls, sequence, turn_start, correlation_id)
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
                        await publish_fragment(sequence, final_text, True, turn_start, correlation_id)
                        break

                except Exception as e:
                    logger.exception(f"Erreur boucle génération: {e}")
                    await publish_fragment(sequence, "Erreur.", True, turn_start, correlation_id)
                    break

            # Enregistrement final, confirmé : le tour suivant peut relire l'historique dans la
            # milliseconde. L'hippocampe traite ses écritures dans l'ordre, donc cet accusé
            # couvre aussi le message utilisateur et les outils écrits plus tôt dans le tour.
            # Il part après le dernier fragment : il ne touche pas le TTFT de ce tour.
            await history_add("assistant", "".join(overall_response_fragments), ack=True)

    await nc.subscribe("io.voice.speak.start", cb=speech_handler(started=True))
    await nc.subscribe("io.voice.speak.end", cb=speech_handler(started=False))
    worker_task = asyncio.create_task(inference_worker())  # gardé en vie : asyncio ne garde qu'une référence faible
    await nc.subscribe("cortex.prompt", queue="lobe_workers", cb=prompt_handler)
    logger.info("👂 Lobe Frontal prêt à foudroyer le TTFT.")
    # Le TUI partage cette même boucle asyncio (docs/adr/0001) : les callbacks NATS
    # ci-dessus continuent de se déclencher normalement pendant que run_async() tourne.
    await tui_app.run_async()

if __name__ == '__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
