from mistralai.client import Mistral
import logging

logger = logging.getLogger("LobeFrontal.MistralInterface")

class MistralInterface:
    def __init__(self, api_key: str):
        try:
            logger.info("Initialisation de l'interface Mistral...")
            self.mistral_client = Mistral(api_key=api_key)
            logger.info("✅ Interface Mistral initialisée.")
        except Exception as e:
            logger.error(f"Erreur d'initialisation de Mistral: {e}")
            return

    async def get_stream(self, messages: list, model, tools, temperature, top_p, reasoning_effort):
        logger.info("Appel de l'interface Mistral...")
        stream = await self.mistral_client.chat.stream_async(
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