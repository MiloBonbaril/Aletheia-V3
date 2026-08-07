# 📚 Hippocampe (the memory)

The hippocampe holds the persistent data of the entity. It is written in **Python**.

## 🎯 Functions

- **Episodic memory:** a PostgreSQL connector that keeps the raw message history (`role`,
  `content`, `timestamp`).
- **Semantic memory (RAG):** a Qdrant vector store. The passive recall and the function calling
  (`save_to_memory` and `get_from_memory`) fill it and read it.
- **Context construction:** for each `hippocampe.context.build` request, the service reads the
  history and does the RAG search **in parallel**. Then it publishes `hippocampe.context.ready`.
- **Failure behavior:** if one source fails, the service publishes an empty context. The
  lobe_frontal continues with no history. It does not wait.

The RAG search starts only if the prompt is not empty. A voice message that carries audio only has
an empty prompt. Thus it does not pay the cost of an embedding search.

The service uses `sentence-transformers` for the embeddings. The collection name is
`aletheia_memory`.

## ⚙️ Configuration and start

### Datastores

```bash
docker compose up -d      # PostgreSQL on 5432, Qdrant on 6333 and 6334
```

This `docker-compose.yml` file is separate from the file at the root of the repository. The root
file starts NATS only.

### Start

```bash
pip install -r requirements.txt
python main.py
```

The NATS address is `nats://localhost:4222` in the source code. The service does not read
`NATS_URL`.

### Environment variables (`.env`)

| Variable | Default | Function |
|---|---|---|
| `POSTGRES_URL` | `postgresql+asyncpg://aletheia:aletheia_password@localhost:5432/hippocampe` | The full database URL. |
| `QDRANT_URL` | `localhost` | The Qdrant host. |
| `QDRANT_PORT` | `6333` | The Qdrant port. |
| `RAG_SCORE_THRESHOLD` | `0.5` | The minimum similarity score for a passive recall. |

The `export_data.py` and `import_data.py` scripts use `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`.

## 🗄️ PostgreSQL schema and migrations

The schema of the `messages` table is in `database.py` only, as the SQLAlchemy model `Message`.
**Alembic** (`migrations/`) keeps the version history. No other file must define this schema.

- **First start, or a new database:** `python main.py` applies the migrations automatically at the
  start. The `init_db()` function calls `alembic upgrade head`. You can also do it manually:
  ```bash
  alembic upgrade head
  ```
- **A database that is older than Alembic**, where the schema is already present: mark it as
  current. Do not run the DDL again:
  ```bash
  alembic stamp head
  ```
- **To change the schema:** edit the `Message` model in `database.py`, then create the migration:
  ```bash
  alembic revision --autogenerate -m "description of the change"
  alembic upgrade head
  ```

The `export_data.py` and `import_data.py` scripts do not create the table. `import_data.py` verifies
that the table exists. If it does not exist, the script tells you to run `alembic upgrade head`.

The history read removes the audio data from the messages before it sends them. A base64 audio blob
in the history makes the `hippocampe.context.ready` message larger than the NATS `max_payload`
limit.

## 🧪 Tests

```bash
pytest tests/
```

The tests cover the removal of the audio blobs from the history.

## 🔌 NATS interface

- **Subscribes to:** `hippocampe.context.build`, `hippocampe.history.add`, `hippocampe.rag.query`
  (request-reply), `hippocampe.rag.add` (request-reply)
- **Publishes on:** `hippocampe.context.ready`
