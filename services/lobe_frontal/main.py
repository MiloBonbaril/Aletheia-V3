import asyncio
import json
import os
import re
import nats
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()

# Configuration
ENABLE_PERSONA = True
DEFAULT_PERSONA = "Tu es Aletheia, une VTubeuse IA autonome, curieuse et un peu sarcastique. Tu réponds de manière courte, naturelle et très dynamique pour un stream."
MODEL = "llama-3.1-8b-instant"

# Global conversation history
conversation_history = []

async def main():
    # Setup Persona
    if ENABLE_PERSONA:
        conversation_history.append({"role": "system", "content": DEFAULT_PERSONA})

    print("🧠 Lobe Frontal en attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        print(f"Erreur de connexion à NATS: {e}")
        return

    print("✅ Lobe Frontal connecté au système nerveux (NATS).")

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        print("⚠️ Attention: GROQ_API_KEY introuvable dans .env")
    
    try:
        groq_client = AsyncGroq(api_key=groq_api_key)
    except Exception as e:
        print(f"Erreur d'initialisation de Groq: {e}")
        return

    # 2. Handler pour les prompts
    async def prompt_handler(msg):
        subject = msg.subject
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        
        print(f"\n[Lobe Frontal] Requête reçue sur '{subject}': {prompt}")
        
        # Add user prompt to history
        conversation_history.append({"role": "user", "content": prompt})

        print("[Lobe Frontal] Génération LLM via Groq en cours...")
        
        try:
            stream = await groq_client.chat.completions.create(
                messages=conversation_history,
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
                            print(f"[Lobe Frontal] Fragment envoyé : {fragment}")
                            sequence += 1

            # Envoyer le reste du buffer s'il y a lieu
            if buffer.strip():
                payload = {
                    "sequence": sequence,
                    "text": buffer.strip(),
                    "is_last": True
                }
                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
                print(f"[Lobe Frontal] Dernier fragment envoyé : {buffer.strip()}")
            else:
                # Envoyer un fragment vide pour signaler la fin si on n'a plus rien
                payload = {
                    "sequence": sequence,
                    "text": "",
                    "is_last": True
                }
                await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
            
            # Add assistant response to history
            conversation_history.append({"role": "assistant", "content": full_response})
            print("[Lobe Frontal] Fin de la génération.")
            
        except Exception as e:
            print(f"[Lobe Frontal] Erreur lors de la génération Groq: {e}")
            payload = {"sequence": 0, "text": f"Erreur de génération.", "is_last": True}
            await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())

    # 4. Souscription aux requêtes du cortex
    await nc.subscribe("cortex.prompt", cb=prompt_handler)
    print("👂 Lobe Frontal en écoute sur 'cortex.prompt'...")

    # Maintien du script en vie
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Arrêt du Lobe Frontal...")
    finally:
        await nc.drain()

if __name__ == '__main__':
    asyncio.run(main())
