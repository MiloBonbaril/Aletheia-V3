import os
import uuid
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

QDRANT_URL = os.getenv("QDRANT_URL", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = "aletheia_memory"
RAG_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.5"))

class RAGManager:
    def __init__(self):
        print("Initializing SentenceTransformers model...")
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')
        # Warm-up: force le chargement complet du modèle ONNX
        self.encoder.encode(["warmup"], show_progress_bar=False)
        self._embedding_dim = self.encoder.get_sentence_embedding_dimension()
        print(f"✅ SentenceTransformers model loaded (dim={self._embedding_dim})")

        self.score_threshold = RAG_SCORE_THRESHOLD
        self.client = AsyncQdrantClient(host=QDRANT_URL, port=QDRANT_PORT)

    async def ensure_collection(self):
        """Vérifie/crée la collection Qdrant. À appeler une fois au démarrage."""
        try:
            collections = await self.client.get_collections()
            names = [coll.name for coll in collections.collections]
            if COLLECTION_NAME not in names:
                await self.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=self._embedding_dim, distance=Distance.COSINE)
                )
                print(f"Collection '{COLLECTION_NAME}' created in Qdrant.")
            else:
                print(f"Collection '{COLLECTION_NAME}' already exists in Qdrant.")
        except Exception as e:
            print(f"Error connecting to Qdrant: {e}. RAG might not work.")

    async def add_memory_async(self, content: str) -> str:
        print(f"[RAG] Adding memory: {content}")
        vector = self.encoder.encode([content], show_progress_bar=False)[0].tolist()
        point_id = str(uuid.uuid4())

        try:
            await self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={"content": content}
                    )
                ]
            )
            return f'Successfully saved: "{content}" in the RAG memory.'
        except Exception as e:
            return f"Error saving memory: {e}"

    async def query_memory_async(self, prompt: str, limit: int = 3, threshold: float = None) -> str:
        print(f"[RAG] Querying memory for: {prompt}")
        vector = self.encoder.encode([prompt], show_progress_bar=False)[0].tolist()
        if threshold is None:
            threshold = self.score_threshold
        try:
            search_result = await self.client.search(
                collection_name=COLLECTION_NAME,
                query_vector=vector,
                limit=limit
            )

            if not search_result:
                print("[RAG] No relevant recent memories found.")
                return ""

            # Filtrage par score de pertinence
            filtered = [hit for hit in search_result if hit.score >= threshold]

            if not filtered:
                print(f"[RAG] All results below threshold ({threshold}).")
                return ""

            results = [hit.payload['content'] for hit in filtered]
            print(f"[RAG] {len(filtered)} results (threshold={threshold}): {results}")
            return "\n".join(results)
        except Exception as e:
            print("[RAG] Error querying memories: ", e)
            return ""

rag_manager = RAGManager()
