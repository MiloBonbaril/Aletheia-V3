# Hippocampe (La Mémoire)

Service gérant les banques de données persistantes de l'entité.

### Rôle principal :
- **Mémoire Épisodique :** Connecteur PostgreSQL stockant l'historique brut des messages (`role`, `content`, `timestamp`).
- **Mémoire Sémantique (RAG) :** Instanciation et requêtage sur une base vectorielle (Qdrant), alimentée par le rappel passif et le Function Calling (`save_to_memory` / `get_from_memory`).

## Schéma PostgreSQL & migrations

Le schéma de la table `messages` est défini une seule fois, dans `database.py` (modèle SQLAlchemy `Message`), et versionné via **Alembic** (`migrations/`). Aucun autre fichier ne doit redéfinir ce schéma.

- **Premier lancement / nouvelle base :** `python main.py` applique automatiquement les migrations au démarrage (`init_db()` appelle `alembic upgrade head`). Vous pouvez aussi l'exécuter manuellement :
  ```bash
  alembic upgrade head
  ```
- **Base existante créée avant l'introduction d'Alembic** (schéma déjà présent) : marquez-la comme à jour sans rejouer le DDL :
  ```bash
  alembic stamp head
  ```
- **Modifier le schéma :** éditez le modèle `Message` dans `database.py`, puis générez la migration correspondante :
  ```bash
  alembic revision --autogenerate -m "description du changement"
  alembic upgrade head
  ```
- `export_data.py` / `import_data.py` ne créent plus la table : `import_data.py` vérifie simplement qu'elle existe et vous invite à lancer `alembic upgrade head` si ce n'est pas le cas.
