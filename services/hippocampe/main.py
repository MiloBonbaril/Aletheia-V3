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
    await rag_manager.ensure_collection()

    print("🧠 Hippocampe en attente de connexion à NATS...")
    try:
        nc = await nats.connect("nats://localhost:4222")
    except Exception as e:
        print(f"Erreur de connexion à NATS: {e}")
        return

    print("✅ Hippocampe connecté au système nerveux (NATS).")

    # ── Handler principal : Construction passive du contexte ──

    async def context_build_handler(msg):
        """
        Reçoit une demande de construction de contexte du Cortex.
        Exécute en PARALLÈLE la récupération de l'historique PostgreSQL
        et la recherche RAG Qdrant, puis publie le résultat complet
        sur hippocampe.context.ready.
        """
        t_start = time.perf_counter()

        try:
            data = json.loads(msg.data.decode())
        except (json.JSONDecodeError, Exception):
            print("[Hippocampe] ⚠️ Payload context.build invalide, ignoré.")
            return

        prompt = data.get("prompt", "")
        correlation_id = data.get("correlation_id", "")
        n_history = data.get("n_history", 20)

        print(f"[Hippocampe] 📥 Context build reçu (corr={correlation_id[:8]}..., prompt_len={len(prompt)})")

        # Exécution parallèle des 2 sources de données
        history_result, rag_result = await asyncio.gather(
            get_recent_history(n_history),
            rag_manager.query_memory_async(prompt) if prompt else _empty_coroutine(),
            return_exceptions=True
        )

        # Gestion des erreurs individuelles
        if isinstance(history_result, Exception):
            print(f"[Hippocampe] ⚠️ Erreur historique: {history_result}")
            history_result = []
        if isinstance(rag_result, Exception):
            print(f"[Hippocampe] ⚠️ Erreur RAG: {rag_result}")
            rag_result = ""

        response = {
            "correlation_id": correlation_id,
            "history": history_result,
            "rag_results": rag_result or "",
            "context_summary": ""  # Stub pour résumé contextuel futur
        }

        await nc.publish("hippocampe.context.ready", json.dumps(response).encode())

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        print(f"[Hippocampe] ✅ Contexte publié (corr={correlation_id[:8]}..., {elapsed_ms:.1f}ms)")

    # ── Écriture historique (fire-and-forget, inchangé) ──

    async def add_history_handler(msg):
        data = json.loads(msg.data.decode())
        role = data.get("role")
        content = data.get("content")
        print(f"[Hippocampe] Ajout historique ({role})")
        if role and content:
            await add_message(role, content)

    # ── RAG : requête active (conservé pour get_from_memory du LLM) ──

    async def rag_query_handler(msg):
        data = json.loads(msg.data.decode())
        prompt = data.get("prompt")
        print(f"[Hippocampe] Requête RAG active reçue: {prompt}")
        result = await rag_manager.query_memory_async(prompt)
        response = {"result": result if result else "No relevant recent memories found."}
        await nc.publish(msg.reply, json.dumps(response).encode())

    # ── RAG : écriture (conservé pour save_to_memory du LLM) ──

    async def rag_add_handler(msg):
        data = json.loads(msg.data.decode())
        content = data.get("content")
        print(f"[Hippocampe] Ajout RAG: {content}")
        result = await rag_manager.add_memory_async(content)
        response = {"result": result}
        await nc.publish(msg.reply, json.dumps(response).encode())

    # ── Souscriptions NATS ──

    await nc.subscribe("hippocampe.context.build", cb=context_build_handler)
    await nc.subscribe("hippocampe.history.add", cb=add_history_handler)
    await nc.subscribe("hippocampe.rag.query", cb=rag_query_handler)
    await nc.subscribe("hippocampe.rag.add", cb=rag_add_handler)

    print("👂 Hippocampe en écoute (mode passif + actif)...")
    print("   - hippocampe.context.build   (construction contexte parallèle)")
    print("   - hippocampe.history.add     (ajout historique)")
    print("   - hippocampe.rag.query       (recherche RAG active / get_from_memory)")
    print("   - hippocampe.rag.add         (ajout RAG / save_to_memory)")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Arrêt de l'Hippocampe...")
    finally:
        await nc.drain()

async def _empty_coroutine():
    """Coroutine vide pour le cas où le prompt est vide."""
    return ""

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Arrêt de l'Hippocampe...")
