import os
import re
import json
import nats
import asyncio
import logging
from datetime import datetime

# Configuration du logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("LobeFrontal")

from nats.errors import ConnectionClosedError, TimeoutError, NoServersError
from dotenv import load_dotenv
from Groq.interface import GroqInterface
from OpenAI.interface import OpenAIInterface
from src.prompt_builder import PromptBuilder

load_dotenv()

nc = None
PUNCTUATION_PATTERN = re.compile(r'([.!?\n]+)')
INFERENCE_SEMAPHORE = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_INFERENCE", "1")))

# Configuration LLM
INTERFACE_NAME = os.getenv("INTERFACE", "groq")
if INTERFACE_NAME == "groq":
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("⚠️ Attention: GROQ_API_KEY introuvable dans .env")
    interface = GroqInterface(groq_api_key)
elif INTERFACE_NAME == "openai" or INTERFACE_NAME == "llama":
    interface = OpenAIInterface()
else:
    logger.error(f"Interface '{INTERFACE_NAME}' non reconnue.")
    raise ValueError(f"Interface '{INTERFACE_NAME}' non reconnue.")

MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
REASONING_EFFORT=os.getenv("REASONING_EFFORT", "default")
TEMPERATURE=float(os.getenv("TEMPERATURE", 0.6))
TOP_P=float(os.getenv("TOP_P", 0.95))
TOP_K=int(os.getenv("TOP_K", 20))
MIN_P=os.getenv("MIN_P", 0)

# Initialisation du PromptBuilder (lecture des fichiers config une seule fois)
prompt_builder = PromptBuilder()


async def save_to_memory(text: str):
    """Save text to RAG memory by calling hippocampe.rag.add"""
    try:
        response = await nc.request("hippocampe.rag.add", json.dumps({"content": text}).encode(), timeout=5.0)
        return json.loads(response.data.decode())["result"]
    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement dans la mémoire: {e}")
        return ""

async def get_from_memory(prompt: str):
    """Get relevant information from memory by calling hippocampe.rag.query"""
    try:
        response = await nc.request("hippocampe.rag.query", json.dumps({"prompt": prompt}).encode(), timeout=5.0)
        return json.loads(response.data.decode())["result"]
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de la mémoire: {e}")
        return ""

async def execute_tool_calls(tool_calls: list, sequence) -> list[dict]:
    results = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            function_name = tool_call.get("function", {}).get("name")
            arguments_str = tool_call.get("function", {}).get("arguments", "{}")
            tool_call_id = tool_call.get("id")
        else:
            function_name = tool_call.function.name
            arguments_str = tool_call.function.arguments
            tool_call_id = tool_call.id

        if not arguments_str:
            arguments_str = "{}"
            
        try:
            function_args = json.loads(arguments_str)
        except json.JSONDecodeError as e:
            logger.error(f"Erreur de décodage JSON pour les arguments du tool {function_name}: {arguments_str} - {e}")
            function_args = {}
            

        logger.info(f"Appel du tool: {function_name}")

        if function_name == "stay_silent":
            logger.info("🤫 Aletheia active le silence radio.")
            # On publie un événement vide pour clore proprement le flux si besoin
            await nc.publish("lobe.fragment_stream", json.dumps({"sequence": sequence, "text": "", "is_last": True}).encode())
            return None # Ou break le while principal si quelque chose derrière

        logger.info(f"Executing tool call: {function_name} with arguments: {function_args}")

        if function_name not in available_functions:
            raise ValueError(f"Unknown tool call: {function_name}")

        function_response = await available_functions[function_name](**function_args)

        results.append(
            {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": function_name,
                "content": function_response,
            }
        )
    return results

available_functions = {
    "save_to_memory": save_to_memory,
    "get_from_memory": get_from_memory
}

async def main():
    global nc
    logger.info("🧠 Lobe Frontal en attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        logger.error(f"Erreur de connexion à NATS: {e}")
        return

    logger.info("✅ Lobe Frontal connecté au système nerveux (NATS).")

    # Handler pour les prompts
    async def prompt_handler(msg):
        subject = msg.subject
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        images = data.get("images", [])
        
        logger.info(f"Requête reçue sur '{subject}': {prompt} (avec {len(images)} images)")
        logger.debug(f"Données brutes reçues : {data}")
        
        # ── Récupération parallèle depuis l'hippocampe ──
        history = []
        context_summary = None

        async def fetch_history():
            nonlocal history
            try:
                response = await nc.request(
                    "hippocampe.history.get",
                    json.dumps({"n": 20}).encode(),
                    timeout=5.0
                )
                history = json.loads(response.data.decode()).get("history", [])
                logger.info(f"Historique récupéré: {len(history)} messages")
            except TimeoutError:
                logger.error("Timeout lors de la récupération de l'historique.")
            except Exception as e:
                logger.error(f"Erreur lors de la récupération de l'historique: {e}")

        async def fetch_context_summary():
            """Stub: récupère le résumé des 10 dernières minutes (à implémenter)."""
            nonlocal context_summary
            try:
                response = await nc.request(
                    "hippocampe.context.summary",
                    b"",
                    timeout=5.0
                )
                result = json.loads(response.data.decode())
                summary = result.get("summary", "")
                if summary:
                    context_summary = summary
                    logger.info("Résumé contextuel récupéré.")
            except TimeoutError:
                logger.debug("Pas de résumé contextuel disponible (timeout).")
            except Exception as e:
                logger.debug(f"Résumé contextuel non disponible: {e}")

        # Exécution parallèle des deux requêtes
        logger.info("Récupération du contexte via Hippocampe...")
        await asyncio.gather(fetch_history(), fetch_context_summary())

        # ── Construction du prompt via PromptBuilder ──
        messages = prompt_builder.build(
            prompt=prompt,
            images=images,
            history=history,
            context_summary=context_summary,
        )

        # ── Sauvegarde du message utilisateur dans l'historique ──
        user_content_for_db = prompt_builder.build_user_content_for_db(prompt, images)
        try:
            logger.debug(f"Enregistrement du prompt utilisateur dans l'historique : {prompt}")
            await nc.publish(
                "hippocampe.history.add",
                json.dumps({"role": "user", "content": user_content_for_db}).encode()
            )
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de l'historique utilisateur: {e}")

        # ── Inférence LLM ──
        logger.info("En attente d'un slot LLM...")
        async with INFERENCE_SEMAPHORE:

            sequence = 0
            overall_response_fragments = []

            MAX_TURNS = 10
            turn_count = 0

            while turn_count < MAX_TURNS:
                turn_count += 1
                logger.debug("--- DÉBUT DES MESSAGES ENVOYÉS AU LLM ---")
                logger.debug(json.dumps(messages, indent=2, ensure_ascii=False))
                logger.debug("--- FIN DES MESSAGES ENVOYÉS AU LLM ---")

                try:
                    stream = await interface.get_stream(
                        messages, MODEL, prompt_builder.tools_schema,
                        TEMPERATURE, TOP_P, REASONING_EFFORT
                    )

                    fragments_buffer = []

                    tool_calls_dict = {}
                    collected_content = ""
                    finish_reason = None

                    async for chunk in stream:
                        if not chunk.choices:
                            continue

                        delta = chunk.choices[0].delta
                        if getattr(delta, "content", None):
                            collected_content += delta.content

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
                                    if getattr(tc_chunk, "id", None):
                                        tool_calls_dict[idx]["id"] = tc_chunk.id
                                    if getattr(tc_chunk.function, "name", None):
                                        tool_calls_dict[idx]["function"]["name"] += tc_chunk.function.name
                                    if getattr(tc_chunk.function, "arguments", None):
                                        tool_calls_dict[idx]["function"]["args_buffer"].append(tc_chunk.function.arguments)

                        if chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason

                        token = getattr(delta, "content", None)
                        if token:
                            fragments_buffer.append(token)
                            overall_response_fragments.append(token)

                            current_buffer_str = "".join(fragments_buffer)
                            # Vérifier si on a une ponctuation forte
                            match = PUNCTUATION_PATTERN.search(current_buffer_str)
                            if match:
                                end_index = match.end()
                                fragment = current_buffer_str[:end_index].strip()
                                fragments_buffer = [current_buffer_str[end_index:]]

                                if fragment:
                                    payload = {
                                        "sequence": sequence,
                                        "text": fragment,
                                        "is_last": False
                                    }
                                    await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                                    logger.debug(f"Fragment envoyé : {fragment}")
                                    sequence += 1

                    for tc in tool_calls_dict.values():
                        if "args_buffer" in tc["function"]:
                            tc["function"]["arguments"] = "".join(tc["function"]["args_buffer"])
                            del tc["function"]["args_buffer"]
                    tool_calls = list(tool_calls_dict.values())
                    if tool_calls and finish_reason == "tool_calls":
                        logger.info(f"Turn {turn_count + 1}: Executing tool calls")

                        assistant_msg = {
                            "role": "assistant",
                            "content": collected_content or None,
                            "tool_calls": tool_calls
                        }
                        messages.append(assistant_msg)

                        results = await execute_tool_calls(tool_calls, sequence)
                        if results is None:
                            return # stay silent
                        # append results to messages
                        messages.extend(results)
                        try:
                            for r in results:
                                if r["name"] == "get_from_memory":
                                    result = {
                                        "role": "tool",
                                        "content": r["content"]
                                    }
                                    await nc.publish("hippocampe.history.add", json.dumps(result).encode())
                        except Exception as e:
                            logger.error(f"Erreur lors de l'enregistrement du résultat du tool: {e}")
                        finish_reason = None
                        continue

                    else:
                        # No more tool calls, flush buffer and we're done
                        current_buffer_str = "".join(fragments_buffer)
                        if current_buffer_str.strip():
                            payload = {
                                "sequence": sequence,
                                "text": current_buffer_str.strip(),
                                "is_last": True
                            }
                            await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                            logger.debug(f"Dernier fragment envoyé : {current_buffer_str.strip()}")
                        else:
                            payload = {
                                "sequence": sequence,
                                "text": "",
                                "is_last": True
                            }
                            await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())

                        break # Exit while loop

                except Exception as e:
                    logger.exception(f"Erreur lors de la génération: {e}")
                    payload = {"sequence": sequence, "text": "Erreur de génération.", "is_last": True}
                    await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                    break

            overall_full_response = "".join(overall_response_fragments)
            try:
                logger.debug(f"Enregistrement de la réponse complète dans l'historique : {overall_full_response}")
                await nc.publish("hippocampe.history.add", json.dumps({"role": "assistant", "content": overall_full_response}).encode())
            except Exception as e:
                logger.error(f"Erreur lors de l'enregistrement de l'historique assistant: {e}")

            logger.info("Fin de la génération (slot libéré).")

    # Souscription aux requêtes du cortex
    await nc.subscribe("cortex.prompt", queue="lobe_workers", cb=prompt_handler)
    logger.info("👂 Lobe Frontal en écoute sur 'cortex.prompt' (queue: lobe_workers)...")

    # Maintien du script en vie
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Arrêt du Lobe Frontal...")
    finally:
        await nc.drain()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt du Lobe Frontal...")
