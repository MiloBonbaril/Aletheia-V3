from groq import AsyncGroq
import logging

logger = logging.getLogger("LobeFrontal.GroqInterface")

class GroqInterface:
    def __init__(self, api_key: str):
        try:
            logger.info("Initialisation de l'interface Groq...")
            self.groq_client = AsyncGroq(api_key=api_key)
            logger.info("✅ Interface Groq initialisée.")
        except Exception as e:
            logger.error(f"Erreur d'initialisation de Groq: {e}")
            return

    async def get_stream(self, messages: list, model, tools, temperature, top_p, reasoning_effort):
        logger.info("Appel de l'interface Groq...")
        stream = await self.groq_client.chat.completions.create(
            messages=messages,
            model=model,
            tools=tools,
            stream=True,
            temperature=temperature,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            #seed=42,
            #logprobs=True,
            #top_logprobs=TOP_K
        )
        return stream