#!/usr/bin/env python3
"""
Import des données Hippocampe depuis un fichier d'export JSON.

Usage:
    python import_data.py 2026-06-12_21-14-57
    python import_data.py ./data/mon_export.json

Déduplique automatiquement :
  - PostgreSQL : par triplet (role, content, timestamp)
  - Qdrant     : par UUID de point
"""

import os
import sys
import json
import argparse
from datetime import datetime

import psycopg2
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
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

def import_postgres(rows: list[dict]) -> tuple[int, int]:
    """
    Importe les messages dans PostgreSQL sans créer de doublons.
    Déduplique sur le triplet (role, content, timestamp).
    Retourne (insérés, ignorés).
    """
    if not rows:
        return 0, 0

    print("📥 Connexion à PostgreSQL...")
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )
    try:
        cur = conn.cursor()

        # Assure que la table existe
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                role VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_messages_timestamp
            ON messages (timestamp DESC);
        """)
        conn.commit()

        # Charge les triplets existants pour déduplication rapide (set lookup O(1))
        cur.execute("SELECT role, content, timestamp FROM messages;")
        existing = set()
        for r in cur.fetchall():
            existing.add((r[0], r[1], r[2].isoformat() if r[2] else None))

        inserted = 0
        skipped = 0

        for row in rows:
            role = row["role"]
            content = row["content"]
            timestamp_str = row.get("timestamp")
            ts = datetime.fromisoformat(timestamp_str) if timestamp_str else None
            ts_key = ts.isoformat() if ts else None

            if (role, content, ts_key) in existing:
                skipped += 1
                continue

            cur.execute(
                "INSERT INTO messages (role, content, timestamp) VALUES (%s, %s, %s);",
                (role, content, ts),
            )
            existing.add((role, content, ts_key))
            inserted += 1

        conn.commit()
        print(f"   ✅ PostgreSQL : {inserted} insérés, {skipped} doublons ignorés.")
        return inserted, skipped
    finally:
        conn.close()


# ── Qdrant ───────────────────────────────────────────────────────────────────

def import_qdrant(points: list[dict]) -> tuple[int, int]:
    """
    Importe les points dans Qdrant sans créer de doublons.
    Déduplique par UUID — un upsert avec le même ID écrase le point,
    mais on skip complètement si le point existe déjà.
    Retourne (insérés, ignorés).
    """
    if not points:
        return 0, 0

    print("📥 Connexion à Qdrant...")
    client = QdrantClient(host=QDRANT_URL, port=QDRANT_PORT)

    # Crée la collection si elle n'existe pas
    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in collections:
        # Détermine la dimension depuis le premier vecteur
        dim = len(points[0]["vector"])
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print(f"   Collection '{QDRANT_COLLECTION}' créée ({dim}d).")

    # Récupère les IDs existants pour déduplication
    existing_ids = set()
    offset = None
    while True:
        result, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            limit=100,
            offset=offset,
            with_vectors=False,
            with_payload=False,
        )
        for pt in result:
            existing_ids.add(str(pt.id))
        if next_offset is None:
            break
        offset = next_offset

    # Filtre les nouveaux points
    new_points = []
    skipped = 0
    for pt in points:
        if str(pt["id"]) in existing_ids:
            skipped += 1
            continue
        new_points.append(
            PointStruct(
                id=pt["id"],
                vector=pt["vector"],
                payload=pt.get("payload", {}),
            )
        )

    # Upsert par batch de 100
    inserted = 0
    for i in range(0, len(new_points), 100):
        batch = new_points[i : i + 100]
        client.upsert(collection_name=QDRANT_COLLECTION, points=batch)
        inserted += len(batch)

    print(f"   ✅ Qdrant : {inserted} insérés, {skipped} doublons ignorés.")
    return inserted, skipped


# ── Main ─────────────────────────────────────────────────────────────────────

def resolve_filepath(name_or_path: str) -> str:
    """Résout le chemin du fichier d'export depuis un nom ou un chemin."""
    # Chemin absolu ou relatif direct
    if os.path.isfile(name_or_path):
        return os.path.abspath(name_or_path)

    # Ajoute .json si besoin
    if not name_or_path.endswith(".json"):
        name_or_path_json = name_or_path + ".json"
    else:
        name_or_path_json = name_or_path

    if os.path.isfile(name_or_path_json):
        return os.path.abspath(name_or_path_json)

    # Cherche dans ./data/
    candidate = os.path.join(DATA_DIR, name_or_path_json)
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)

    return None


def main():
    parser = argparse.ArgumentParser(description="Import des données Hippocampe")
    parser.add_argument(
        "file",
        help="Nom de l'export (sans extension) ou chemin vers le fichier .json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche ce qui serait importé sans écrire.",
    )
    args = parser.parse_args()

    filepath = resolve_filepath(args.file)
    if filepath is None:
        print(f"❌ Fichier introuvable : {args.file}")
        print(f"   Cherché dans : ./ et {DATA_DIR}/")
        sys.exit(1)

    print(f"📂 Lecture de {filepath}...")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    version = data.get("version", 0)
    if version != 1:
        print(f"⚠️  Version de format inconnue ({version}), tentative d'import quand même...")

    pg_rows = data.get("postgres", {}).get("rows", [])
    qd_points = data.get("qdrant", {}).get("points", [])

    print(f"\n📊 Contenu de l'export :")
    print(f"   PostgreSQL : {len(pg_rows)} messages")
    print(f"   Qdrant     : {len(qd_points)} points")
    print(f"   Exporté le : {data.get('exported_at', 'inconnu')}\n")

    if args.dry_run:
        print("🔍 Mode dry-run — aucune modification effectuée.")
        return

    pg_ins, pg_skip = import_postgres(pg_rows)
    qd_ins, qd_skip = import_qdrant(qd_points)

    print(f"\n✅ Import terminé :")
    print(f"   PostgreSQL : {pg_ins} insérés, {pg_skip} doublons ignorés")
    print(f"   Qdrant     : {qd_ins} insérés, {qd_skip} doublons ignorés")


if __name__ == "__main__":
    main()
