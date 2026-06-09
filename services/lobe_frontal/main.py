import os
import re
import json
import nats
import asyncio
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("LobeFrontal")

from dotenv import load_dotenv
from OpenAI.interface import OpenAIInterface
from src.prompt_builder import PromptBuilder

load_dotenv()

nc = None
PUNCTUATION_PATTERN = re.compile(r'([.!?\n]+)')
INFERENCE_SEMAPHORE = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_INFERENCE", "1")))

# Configuration LLM forcée sur notre interface optimisée
interface = OpenAIInterface()
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
TEMPERATURE = float(os.getenv("TEMPERATURE", 0.6))
TOP_P = float(os.getenv("TOP_P", 0.95))
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "default")

prompt_builder = PromptBuilder()

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

async def execute_tool_calls(tool_calls: list, sequence) -> list[dict] | None:
    """Exécute tous les appels d'outils en PARALLÈLE pour maximiser le débit."""
    
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
            return "SILENT", tc_id, f_name, None

        try:
            f_args = json.loads(args_str) if args_str else {}
        except Exception:
            f_args = {}

        if f_name not in available_functions:
            return "ERROR", tc_id, f_name, f"Unknown tool: {f_name}"
        
        logger.info(f"🚀 Lancement de l'outil en arrière-plan: {f_name}")
        res = await available_functions[f_name](**f_args)
        return "OK", tc_id, f_name, res

    # Lancement simultané de toutes les tâches d'outils
    tasks = [run_single_tool(tc) for tc in tool_calls]
    completed_tasks = await asyncio.gather(*tasks)

    results = []
    for status, tc_id, name, content in completed_tasks:
        if status == "SILENT":
            logger.info("🤫 Silence radio activé.")
            await nc.publish("lobe.fragment_stream", json.dumps({"sequence": sequence, "text": "", "is_last": True}).encode())
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
    try:
        models = await interface.get_models()
        logger.info(f"✅ Connecté au serveur d'inférence! Modèles disponibles: {models}")
    except Exception as e:
        logger.error(f"Erreur lors de la connexion au serveur d'inférence: {e}")
        raise Exception("Erreur lors de la connexion au serveur d'inférence")

    async def prompt_handler(msg):
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        images = data.get("images", [])
        audio = data.get("audio", None)
        
        history = []
        context_summary = None

        async def fetch_history():
            nonlocal history
            try:
                res = await nc.request("hippocampe.history.get", json.dumps({"n": 20}).encode(), timeout=3.0)
                history = json.loads(res.data.decode()).get("history", [])
            except Exception as e: logger.error(f"Erreur historique: {e}")

        async def fetch_context_summary():
            nonlocal context_summary
            try:
                res = await nc.request("hippocampe.context.summary", b"", timeout=3.0)
                context_summary = json.loads(res.data.decode()).get("summary")
            except Exception: pass

        # Récupération parallèle
        await asyncio.gather(fetch_history(), fetch_context_summary())

        messages = prompt_builder.build(prompt, images, audio, history, context_summary)

        # Log asynchrone du prompt utilisateur en tâche de fond
        asyncio.create_task(nc.publish(
            "hippocampe.history.add",
            json.dumps({"role": "user", "content": prompt_builder.build_user_content_for_db(prompt, images, audio)}).encode()
        ))

        async with INFERENCE_SEMAPHORE:
            sequence = 0
            overall_response_fragments = []
            turn_count = 0

            while turn_count < 10:
                turn_count += 1
                try:
                    stream = await interface.get_stream(messages, MODEL, prompt_builder.tools_schema, TEMPERATURE, TOP_P, REASONING_EFFORT)
                    
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
                                    await nc.publish("lobe.fragment_stream", json.dumps({
                                        "sequence": sequence, "text": fragment, "is_last": False
                                    }).encode())
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
                    
                    if tool_calls and finish_reason == "tool_calls":
                        messages.append({
                            "role": "assistant",
                            "content": collected_content or None,
                            "tool_calls": tool_calls
                        })
                        
                        results = await execute_tool_calls(tool_calls, sequence)
                        if results is None: return # stay silent
                        
                        messages.extend(results)
                        
                        # Historisation asynchrone en tâche de fond pour ne pas bloquer le tour de boucle
                        for r in results:
                            if r["name"] == "get_from_memory":
                                asyncio.create_task(nc.publish("hippocampe.history.add", json.dumps({"role": "tool", "content": r["content"]}).encode()))
                        
                        finish_reason = None
                        continue
                    else:
                        # Flush final du buffer glissant s'il reste du texte
                        final_text = text_buffer.strip()
                        await nc.publish("lobe.fragment_stream", json.dumps({
                            "sequence": sequence, 
                            "text": final_text, 
                            "is_last": True
                        }).encode())
                        break

                except Exception as e:
                    logger.exception(f"Erreur boucle génération: {e}")
                    await nc.publish("lobe.fragment_stream", json.dumps({"sequence": sequence, "text": "Erreur.", "is_last": True}).encode())
                    break

            # Enregistrement final asynchrone
            asyncio.create_task(nc.publish(
                "hippocampe.history.add", 
                json.dumps({"role": "assistant", "content": "".join(overall_response_fragments)}).encode()
            ))

    await nc.subscribe("cortex.prompt", queue="lobe_workers", cb=prompt_handler)
    logger.info("👂 Lobe Frontal prêt à foudroyer le TTFT.")
    while True: await asyncio.sleep(1)

if __name__ == '__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
