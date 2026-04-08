import asyncio
import json
import nats
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError

async def main():
    # 1. Connexion au serveur NATS
    print("🧠 Lobe Frontal en attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        print(f"Erreur de connexion à NATS: {e}")
        return

    print("✅ Lobe Frontal connecté au système nerveux (NATS).")

    # 2. Handler pour les prompts
    async def prompt_handler(msg):
        subject = msg.subject
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt", "")
        
        print(f"\n[Lobe Frontal] Requête reçue sur '{subject}': {prompt}")
        print("[Lobe Frontal] Simulation de génération LLM en cours...")

        # Simulation d'une latence d'inférence (génération du premier token)
        await asyncio.sleep(1.0)
        
        # Réponse factice
        fake_response = [
            "Bonjour Cortex,",
            " je suis le Lobe Frontal.",
            " Je traite ta requête,",
            " et j'envoie ceci morceau par morceau,",
            " pour simuler le streaming token par token !"
        ]

        # 3. Streaming des fragments au Cortex
        for index, fragment in enumerate(fake_response):
            # Découpage et latence "per token / fragment"
            await asyncio.sleep(0.5)
            
            payload = {
                "sequence": index,
                "text": fragment,
                "is_last": index == len(fake_response) - 1
            }
            
            await nc.publish("lobe.fragment_stream", json.dumps(payload).encode())
            print(f"[Lobe Frontal] Fragment envoyé : {fragment}")

        print("[Lobe Frontal] Fin de la génération.")


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
