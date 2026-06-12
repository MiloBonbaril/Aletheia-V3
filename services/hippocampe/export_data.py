#!/usr/bin/env python3
"""
Export complet des bases de données Hippocampe (PostgreSQL + Qdrant).

Usage:
    python export_data.py                          # nom = horodatage actuel
    python export_data.py mon_export               # nom personnalisé
    python export_data.py --name mon_export        # idem, avec flag

Le fichier est créé dans ./data/{nom}.json
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

import psycopg2
from qdrant_client import QdrantClient
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "aletheia")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "aletheia_password")
POSTGRES_DB = os.getenv("POSTGRES_DB", "hippocampe")

QDRANT_URL = os.getenv("QDRANT_URL", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "aletheia_memory"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ── PostgreSQL ───────────────────────────────────────────────────────────────

def export_postgres() -> list[dict]:
    """Exporte tous les messages de la table `messages`."""
    print("📦 Connexion à PostgreSQL...")
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, role, content, timestamp FROM messages ORDER BY id ASC;")
        rows = cur.fetchall()
        messages = []
        for row in rows:
            messages.append({
                "id": row[0],
                "role": row[1],
                "content": row[2],
                "timestamp": row[3].isoformat() if row[3] else None,
            })
        print(f"   ✅ {len(messages)} messages exportés depuis PostgreSQL.")
        return messages
    finally:
        conn.close()


# ── Qdrant ───────────────────────────────────────────────────────────────────

def export_qdrant() -> list[dict]:
    """Exporte tous les points de la collection Qdrant (vecteurs + payloads)."""
    print("📦 Connexion à Qdrant...")
    client = QdrantClient(host=QDRANT_URL, port=QDRANT_PORT)

    # Vérifie que la collection existe
    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in collections:
        print(f"   ⚠️  Collection '{QDRANT_COLLECTION}' introuvable — aucun point à exporter.")
        return []

    # Scroll exhaustif (par pages de 100)
    points = []
    offset = None
    while True:
        result, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=100,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        for point in result:
            points.append({
                "id": str(point.id),
                "vector": point.vector,
                "payload": point.payload,
            })
        if next_offset is None:
            break
        offset = next_offset

    print(f"   ✅ {len(points)} points exportés depuis Qdrant.")
    return points


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Export des données Hippocampe")
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Nom du fichier d'export (sans extension). Par défaut : horodatage actuel.",
    )
    parser.add_argument(
        "--name", "-n",
        dest="name_flag",
        default=None,
        help="Nom du fichier d'export (alternative au positional).",
    )
    args = parser.parse_args()

    export_name = args.name_flag or args.name
    if export_name is None:
        export_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Assure que le nom n'a pas d'extension
    if export_name.endswith(".json"):
        export_name = export_name[:-5]

    os.makedirs(DATA_DIR, exist_ok=True)
    filepath = os.path.join(DATA_DIR, f"{export_name}.json")

    print(f"🚀 Export Hippocampe → {filepath}\n")

    # Export des deux sources
    pg_messages = export_postgres()
    qdrant_points = export_qdrant()

    export_data = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "postgres": {
            "table": "messages",
            "count": len(pg_messages),
            "rows": pg_messages,
        },
        "qdrant": {
            "collection": QDRANT_COLLECTION,
            "count": len(qdrant_points),
            "points": qdrant_points,
        },
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"\n✅ Export terminé : {filepath} ({size_mb:.2f} Mo)")
    print(f"   PostgreSQL : {len(pg_messages)} messages")
    print(f"   Qdrant     : {len(qdrant_points)} points")


if __name__ == "__main__":
    main()
