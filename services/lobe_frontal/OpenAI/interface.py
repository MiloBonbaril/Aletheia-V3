import logging
import httpx
from openai import AsyncOpenAI

logger = logging.getLogger("LobeFrontal.OpenAIInterface")

class OpenAIInterface:
    def __init__(self):
        try:
            logger.info("Initialisation de l'interface OpenAI haute performance...")
            # Configuration d'un pool de connexion agressif pour éliminer la latence TCP
            http_client = httpx.AsyncClient(
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                transport=httpx.AsyncHTTPTransport(retries=1),
                timeout=30.0
            )
            self.openai_client = AsyncOpenAI(
                base_url="http://127.0.0.1:8080/v1", 
                api_key="sk-vide",
                http_client=http_client
            )
            logger.info("✅ Interface OpenAI initialisée.")
        except Exception as e:
            logger.error(f"Erreur d'initialisation de OpenAI: {e}")

    async def get_models(self):
        return await self.openai_client.models.list()

    async def get_stream(self, messages: list, model, tools, temperature, top_p, reasoning_effort):
        logger.info("Envoi immédiat de la requête LLM...")
        return await self.openai_client.chat.completions.create(
            messages=messages,
            model=model,
            tools=tools if tools else None,  # Évite d'envoyer un tableau vide inutile
            stream=True,
            temperature=temperature,
            top_p=top_p,
            reasoning_effort=reasoning_effort
        )