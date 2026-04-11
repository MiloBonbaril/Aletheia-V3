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
        "description": "Called to save text to RAG memory",
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
        "description": "Called to get relevant information from memory",
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
                
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                        
                    delta = chunk.choices[0].delta
                    
                    if getattr(delta, "tool_calls", None):
                        for tc in delta.tool_calls:
                            while len(tool_calls) <= tc.index:
                                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if getattr(tc, "id", None):
                                tool_calls[tc.index]["id"] = tc.id
                            if getattr(tc, "function", None):
                                if getattr(tc.function, "name", None):
                                    tool_calls[tc.index]["function"]["name"] += tc.function.name
                                if getattr(tc.function, "arguments", None):
                                    tool_calls[tc.index]["function"]["arguments"] += tc.function.arguments
                    
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

                if tool_calls:
                    # Append assistant message with tool calls
                    assistant_msg = {
                        "role": "assistant",
                        "tool_calls": tool_calls
                    }
                    if turn_response:
                        assistant_msg["content"] = turn_response
                    messages.append(assistant_msg)
                    
                    for tc in tool_calls:
                        logger.info(f"Appel du tool: {tc['function']['name']}")
                        function_name = tc["function"]["name"]
                        function_to_call = available_functions.get(function_name)
                        if function_to_call:
                            try:
                                arguments = json.loads(tc["function"]["arguments"])
                                result = await function_to_call(**arguments)
                            except json.JSONDecodeError as e:
                                result = f"Erreur de décodage JSON des arguments : {e}"
                                logger.error(f"Erreur JSON tool {function_name}: {e}\nArguments: {tc['function']['arguments']}")
                            except Exception as e:
                                result = f"Erreur de la fonction : {e}"
                                logger.error(f"Erreur d'exécution tool {function_name}: {e}")
                        else:
                            result = f"Erreur: Fonction inconnue {function_name}"
                            logger.error(f"Fonction introuvable : {function_name}")
                            
                        logger.debug(f"Résultat du tool {function_name} : {result}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": function_name,
                            "content": str(result)
                        })
                        
                    # Continue loop to call LLM again with tool results
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
