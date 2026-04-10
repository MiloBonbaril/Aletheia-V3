import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

QDRANT_URL = os.getenv("QDRANT_URL", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = "aletheia_memory"

class RAGManager:
    def __init__(self):
        print("Initializing SentenceTransformers model...")
        self.encoder = SentenceTransformer('all-MiniLM-L6-v2')
        
        try:
            self.client = QdrantClient(host=QDRANT_URL, port=QDRANT_PORT)
            collections = [coll.name for coll in self.client.get_collections().collections]
            
            if COLLECTION_NAME not in collections:
                embedding_dim = self.encoder.get_sentence_embedding_dimension()
                self.client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE)
                )
                print(f"Collection '{COLLECTION_NAME}' created in Qdrant.")
        except Exception as e:
            print(f"Error connecting to Qdrant: {e}. RAG might not work.")

    def add_memory(self, content: str):
        print(f"[RAG] Adding memory: {content}")
        vector = self.encoder.encode([content])[0].tolist()
        point_id = str(uuid.uuid4())
        
        try:
            self.client.upsert(
                collection_name=COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={"content": content}
                    )
                ]
            )
            return "Memory saved successfully."
        except Exception as e:
            return f"Error saving memory: {e}"

    def query_memory(self, prompt: str, limit: int = 3):
        print(f"[RAG] Querying memory for: {prompt}")
        vector = self.encoder.encode([prompt])[0].tolist()
        print("[RAG] vector created")

        try:
            search_result = self.client.search(
                collection_name=COLLECTION_NAME,
                query_vector=vector,
                limit=limit
            )
            
            if not search_result:
                print("[RAG] No relevant recent memories found.")
                return "No relevant recent memories found."
            
            results = [hit.payload['content'] for hit in search_result]
            print("[RAG] results : ", results)
            return "\n".join(results)
        except Exception as e:
            print("[RAG] Error querying memories: ", e)
            return f"Error querying memories: {e}"

rag_manager = RAGManager()
