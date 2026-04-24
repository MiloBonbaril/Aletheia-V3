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

    # ── Historique ──

    async def get_history_handler(msg):
        """Retourne les N derniers messages de l'historique."""
        try:
            data = json.loads(msg.data.decode())
            n = data.get("n", 20)
        except (json.JSONDecodeError, Exception):
            n = 20

        print(f"[Hippocampe] Requête historique reçue (n={n})")
        history = await get_recent_history(n)
        response = {"history": history}
        await nc.publish(msg.reply, json.dumps(response).encode())

    async def add_history_handler(msg):
        data = json.loads(msg.data.decode())
        role = data.get("role")
        content = data.get("content")
        print(f"[Hippocampe] Ajout historique ({role})")
        if role and content:
            await add_message(role, content)

    # ── Résumé contextuel (stub) ──

    async def context_summary_handler(msg):
        """
        Stub: retourne un résumé des 10 dernières minutes.
        À implémenter plus tard avec un vrai résumé LLM.
        Pour l'instant retourne un résumé vide.
        """
        print("[Hippocampe] Requête de résumé contextuel reçue (stub).")
        response = {"summary": ""}
        await nc.publish(msg.reply, json.dumps(response).encode())

    # ── RAG ──

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

    # ── Souscriptions NATS ──

    await nc.subscribe("hippocampe.history.get", cb=get_history_handler)
    await nc.subscribe("hippocampe.history.add", cb=add_history_handler)
    await nc.subscribe("hippocampe.context.summary", cb=context_summary_handler)
    await nc.subscribe("hippocampe.rag.query", cb=rag_query_handler)
    await nc.subscribe("hippocampe.rag.add", cb=rag_add_handler)
    
    print("👂 Hippocampe en écoute...")
    print("   - hippocampe.history.get     (récupération historique)")
    print("   - hippocampe.history.add     (ajout historique)")
    print("   - hippocampe.context.summary (résumé 10 min — stub)")
    print("   - hippocampe.rag.query       (recherche RAG)")
    print("   - hippocampe.rag.add         (ajout RAG)")

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
