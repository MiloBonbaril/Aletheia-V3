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

# Configuration
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

async def main():
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
        logger.debug("--- DÉBUT DES MESSAGES ENVOYÉS AU LLM ---")
        logger.debug(json.dumps(messages, indent=2, ensure_ascii=False))
        logger.debug("--- FIN DES MESSAGES ENVOYÉS AU LLM ---")
        
        try:
            stream = await groq_client.chat.completions.create(
                messages=messages,
                model=MODEL,
                stream=True,
            )
            
            buffer = ""
            sequence = 0
            full_response = ""
            
            # Ponctuation forte pour découper les phrases
            punctuation_pattern = re.compile(r'([.!?\n]+)')
            
            async for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    buffer += token
                    full_response += token
                    
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

            # Envoyer le reste du buffer s'il y a lieu
            if buffer.strip():
                payload = {
                    "sequence": sequence,
                    "text": buffer.strip(),
                    "is_last": True
                }
                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                logger.debug(f"Dernier fragment envoyé : {buffer.strip()}")
            else:
                # Envoyer un fragment vide pour signaler la fin si on n'a plus rien
                payload = {
                    "sequence": sequence,
                    "text": "",
                    "is_last": True
                }
                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
            
            try:
                logger.debug(f"Enregistrement de la réponse complète dans l'historique : {full_response}")
                await nc.publish("hippocampe.history.add", json.dumps({"role": "assistant", "content": full_response}).encode())
            except Exception as e:
                logger.error(f"Erreur lors de l'enregistrement de l'historique assistant: {e}")
                
            logger.info("Fin de la génération.")
            
        except Exception as e:
            logger.exception(f"Erreur lors de la génération Groq: {e}")
            payload = {"sequence": 0, "text": f"Erreur de génération.", "is_last": True}
            await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())

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
