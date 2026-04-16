import asyncio
import json
import nats
import os
import time
from database import init_db, add_message, get_recent_history
from rag_manager import rag_manager

# Init db with retry for docker startup
async def try_init_db():
    max_retries = 5
    for i in range(max_retries):
        try:
            await init_db()
            print("Base de données initialisée.")
            return
        except Exception as e:
            print(f"La base de données n'est pas encore prête... ({i+1}/{max_retries})")
            await asyncio.sleep(2)
    print("Échec de connexion à la base de données après plusieurs tentatives.")
async def main():
    await try_init_db()

    print("🧠 Hippocampe en attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        print(f"Erreur de connexion à NATS: {e}")
        return

    print("✅ Hippocampe connecté au système nerveux (NATS).")

    def read_context_files():
        memory_content = "Il n'y a pas de mémoire importante définie."
        user_content = "Aucune information utilisateur n'est définie."
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(script_dir, "PERSONA.md"), "r") as f:
                persona_content = f.read()
        except FileNotFoundError:
            pass

        try:
            with open(os.path.join(script_dir, "MEMORY.md"), "r") as f:
                memory_content = f.read()
        except FileNotFoundError:
            pass

        try:
            with open(os.path.join(script_dir, "USER.md"), "r") as f:
                user_content = f.read()
        except FileNotFoundError:
            pass
            
        return f"{persona_content}\n\nMémoire importante:\n{memory_content}\n\nInformations sur ton humain (ton utilisateur):\n{user_content}\n"
        
    async def get_context_handler(msg):
        print("[Hippocampe] Requête de contexte reçue.")
        system_prompt = read_context_files()
        history = await get_recent_history(20)
        
        response = {
            "system_prompt": system_prompt,
            "history": history
        }
        await nc.publish(msg.reply, json.dumps(response).encode())

    async def add_history_handler(msg):
        data = json.loads(msg.data.decode())
        role = data.get("role")
        content = data.get("content")
        print(f"[Hippocampe] Ajout historique ({role})")
        if role and content:
            await add_message(role, content)

    async def rag_query_handler(msg):
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt")
        print(f"[Hippocampe] Requête RAG reçue: {prompt}")
        result = rag_manager.query_memory(prompt)
        response = {"result": result}
        await nc.publish(msg.reply, json.dumps(response).encode())

    async def rag_add_handler(msg):
        data = json.loads(msg.data.decode())
        content = data.get("content")
        print(f"[Hippocampe] Ajout RAG: {content}")
        result = rag_manager.add_memory(content)
        response = {"result": result}
        await nc.publish(msg.reply, json.dumps(response).encode())

    await nc.subscribe("hippocampe.context.get", cb=get_context_handler)
    await nc.subscribe("hippocampe.history.add", cb=add_history_handler)
    await nc.subscribe("hippocampe.rag.query", cb=rag_query_handler)
    await nc.subscribe("hippocampe.rag.add", cb=rag_add_handler)
    
    print("👂 Hippocampe en écoute...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Arrêt de l'Hippocampe...")
    finally:
        await nc.drain()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Arrêt de l'Hippocampe...")
