import asyncio
import json
import os
import re
import nats
import logging

# Configuration du logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("LobeFrontal")
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

nc = None

# Configuration
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# Tool declarations
tools = [
    {
        "type": "function",
        "function": {
            "name": "save_to_memory",
            "description": "Call this tool to save text to RAG memory, please use it to save only important memories",
            "parameters": {
            "type": "object",
            "properties": {
                "text": {
                "type": "string",
                "description": "The text to save to memory"
                }
            },
            "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_from_memory",
            "description": "Call this tool to get relevant information to a prompt or a word from RAG memory. Please call this tool (if needed) before answering something.",
            "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                "type": "string",
                "description": "The prompt or word to search related information from memory"
                }
            },
            "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stay_silent",
            "description": "Call this tool IMMEDIATELY if you decide you do not want to respond to the user, if you are ignoring them, or if you were explicitly told to be quiet. Do not output any text if you call this.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


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

async def execute_tool_calls(tool_calls: list[dict], sequence) -> list[dict]:
    results = []
    for tool_call in tool_calls:
        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)
        tool_call_id = tool_call.id
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

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        logger.warning("⚠️ Attention: GROQ_API_KEY introuvable dans .env")
    
    try:
        groq_client = AsyncGroq(api_key=groq_api_key)
    except Exception as e:
        logger.error(f"Erreur d'initialisation de Groq: {e}")
        return

    # 2. Handler pour les prompts
    async def prompt_handler(msg):
        subject = msg.subject
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        
        logger.info(f"Requête reçue sur '{subject}': {prompt}")
        logger.debug(f"Données brutes reçues : {data}")
        
        logger.info("Récupération du contexte via Hippocampe...")
        messages = []
        try:
            response = await nc.request("hippocampe.context.get", b"", timeout=5.0)
            context = json.loads(response.data.decode())
            system_prompt = context.get("system_prompt", "")
            history = context.get("history", [])
            
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            for msg_entry in history:
                messages.append({"role": msg_entry["role"], "content": msg_entry["content"]})
                
        except TimeoutError:
            logger.error("Timeout lors de la récupération du contexte.")
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du contexte: {e}")
            
        messages.append({"role": "user", "content": prompt})
        
        try:
            logger.debug(f"Enregistrement du prompt utilisateur dans l'historique : {prompt}")
            await nc.publish("hippocampe.history.add", json.dumps({"role": "user", "content": prompt}).encode())
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de l'historique utilisateur: {e}")

        logger.info("Génération LLM via Groq en cours...")
        
        sequence = 0
        overall_full_response = ""
        
        MAX_TURNS = 5
        turn_count = 0
        
        while turn_count < MAX_TURNS:
            turn_count += 1
            logger.debug("--- DÉBUT DES MESSAGES ENVOYÉS AU LLM ---")
            logger.debug(json.dumps(messages, indent=2, ensure_ascii=False))
            logger.debug("--- FIN DES MESSAGES ENVOYÉS AU LLM ---")
            
            try:
                stream = await groq_client.chat.completions.create(
                    messages=messages,
                    model=MODEL,
                    tools=tools,
                    stream=True,
                )
                
                buffer = ""
                turn_response = ""
                
                # Ponctuation forte pour découper les phrases
                punctuation_pattern = re.compile(r'([.!?\n]+)')
                
                tool_calls = []
                collected_content = ""
                finish_reason = None
                
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                        
                    delta = chunk.choices[0].delta
                    if chunk.choices[0].delta.content:
                        collected_content += chunk.choices[0].delta.content
                    if chunk.choices[0].delta.tool_calls:
                        tool_calls.extend(chunk.choices[0].delta.tool_calls)
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason

                    token = getattr(delta, "content", None)
                    if token:
                        buffer += token
                        turn_response += token
                        overall_full_response += token
                        
                        # Vérifier si on a une ponctuation forte
                        match = punctuation_pattern.search(buffer)
                        if match:
                            end_index = match.end()
                            fragment = buffer[:end_index].strip()
                            buffer = buffer[end_index:]
                            
                            if fragment:
                                payload = {
                                    "sequence": sequence,
                                    "text": fragment,
                                    "is_last": False
                                }
                                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                                logger.debug(f"Fragment envoyé : {fragment}")
                                sequence += 1

                if tool_calls and finish_reason == "tool_calls":
                    logger.info(f"Turn {turn_count + 1}: Executing tool calls")
                    results = await execute_tool_calls(tool_calls, sequence)
                    if results is None:
                        return # stay silent
                    # append results to messages
                    messages.extend(results)
                    turn_count += 1
                    continue

                else:
                    # No more tool calls, flush buffer and we're done
                    if buffer.strip():
                        payload = {
                            "sequence": sequence,
                            "text": buffer.strip(),
                            "is_last": True
                        }
                        await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                        logger.debug(f"Dernier fragment envoyé : {buffer.strip()}")
                    else:
                        payload = {
                            "sequence": sequence,
                            "text": "",
                            "is_last": True
                        }
                        await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                    
                    break # Exit while loop
                    
            except Exception as e:
                logger.exception(f"Erreur lors de la génération Groq: {e}")
                payload = {"sequence": sequence, "text": "Erreur de génération.", "is_last": True}
                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                break
                
        try:
            logger.debug(f"Enregistrement de la réponse complète dans l'historique : {overall_full_response}")
            await nc.publish("hippocampe.history.add", json.dumps({"role": "assistant", "content": overall_full_response}).encode())
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de l'historique assistant: {e}")
            
        logger.info("Fin de la génération.")

    # 4. Souscription aux requêtes du cortex
    await nc.subscribe("cortex.prompt", cb=prompt_handler)
    logger.info("👂 Lobe Frontal en écoute sur 'cortex.prompt'...")

    # Maintien du script en vie
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Arrêt du Lobe Frontal...")
    finally:
        await nc.drain()

if __name__ == '__main__':
    asyncio.run(main())
