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

    async def get_model_details(self, model_id: str) -> dict:
        """
        Récupère les informations détaillées d'un modèle, y compris
        ses modes de raisonnement (reasoning_effort) supportés.
        
        Combine les métadonnées SDK avec un appel HTTP brut au serveur
        d'inférence pour capturer les champs étendus (context_length,
        capabilities, reasoning efforts, etc.).
        """
        details = {
            "id": model_id,
            "object": None,
            "created": None,
            "owned_by": None,
            "context_length": None,
            "capabilities": {},
            "supported_reasoning_efforts": [],
            "raw_server_metadata": {},
        }

        # 1. Métadonnées via le SDK (liste + filtre, car /v1/models/{id} n'est
        #    pas supporté par tous les serveurs, notamment llama.cpp)
        try:
            models_list = await self.openai_client.models.list()
            model_obj = next((m for m in models_list.data if m.id == model_id), None)
            if model_obj:
                details["object"] = model_obj.object
                details["created"] = model_obj.created
                details["owned_by"] = model_obj.owned_by
                # Champs étendus exposés par certains serveurs (vLLM, etc.)
                for attr in ("context_length", "max_model_len", "max_seq_len"):
                    val = getattr(model_obj, attr, None)
                    if val is not None:
                        details["context_length"] = val
                        break
            else:
                logger.warning(f"Modèle '{model_id}' non trouvé dans la liste des modèles")
        except Exception as e:
            logger.warning(f"SDK models.list échoué: {e}")

        # 2. Appel HTTP brut sur /v1/models pour récupérer les métadonnées
        #    étendues du serveur (llama.cpp n'expose que cet endpoint)
        try:
            http_client = self.openai_client._client  # httpx.AsyncClient sous-jacent
            response = await http_client.get("/v1/models")
            if response.status_code == 200:
                all_models = response.json()
                # Chercher notre modèle dans la liste
                raw = None
                for entry in all_models.get("data", []):
                    if entry.get("id") == model_id:
                        raw = entry
                        break

                if raw:
                    details["raw_server_metadata"] = raw

                    # Extraction du context_length depuis les métadonnées brutes
                    if details["context_length"] is None:
                        for key in ("context_length", "max_model_len", "max_seq_len", "context_window"):
                            if key in raw:
                                details["context_length"] = raw[key]
                                break

                    # Extraction des reasoning efforts supportés
                    reasoning = raw.get("supported_reasoning_efforts") or raw.get("reasoning_efforts")
                    if reasoning:
                        details["supported_reasoning_efforts"] = reasoning
                    elif raw.get("capabilities", {}).get("reasoning"):
                        details["supported_reasoning_efforts"] = raw["capabilities"]["reasoning"]

                    # Extraction des capacités générales
                    if "capabilities" in raw:
                        details["capabilities"] = raw["capabilities"]
                else:
                    logger.warning(f"Modèle '{model_id}' absent des métadonnées HTTP brutes")
            else:
                logger.warning(f"HTTP /v1/models → {response.status_code}")
        except Exception as e:
            logger.warning(f"Appel HTTP brut échoué: {e}")

        # 3. Fallback : si aucun reasoning effort n'a été trouvé, fournir les valeurs standard OpenAI
        if not details["supported_reasoning_efforts"]:
            details["supported_reasoning_efforts"] = ["none", "low", "medium", "high"]
            details["_reasoning_efforts_source"] = "fallback_standard"
        else:
            details["_reasoning_efforts_source"] = "server"

        logger.info(f"📋 Détails du modèle '{model_id}': context={details['context_length']}, "
                     f"reasoning_efforts={details['supported_reasoning_efforts']} "
                     f"(source: {details['_reasoning_efforts_source']})")

        return details

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