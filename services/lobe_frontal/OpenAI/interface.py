from openai import AsyncOpenAI
import logging

logger = logging.getLogger("LobeFrontal.OpenAIInterface")

class OpenAIInterface:
    def __init__(self):
        try:
            logger.info("Initialisation de l'interface OpenAI...")
            self.openai_client = AsyncOpenAI(base_url="http://192.168.1.2:8080/v1", api_key="sk-vide", timeout=30.0)
            logger.info("✅ Interface OpenAI initialisée.")
        except Exception as e:
            logger.error(f"Erreur d'initialisation de OpenAI: {e}")
            return

    async def get_stream(self, messages: list, model, tools, temperature, top_p, reasoning_effort):
        logger.info("Appel de l'interface OpenAI...")
        stream = await self.openai_client.chat.completions.create(
            messages=messages,
            model=model,
            tools=tools,
            stream=True,
            temperature=temperature,
            top_p=top_p,
            #reasoning_effort=reasoning_effort,
            #seed=42,
            #logprobs=True,
            #top_logprobs=TOP_K
        )
        return stream