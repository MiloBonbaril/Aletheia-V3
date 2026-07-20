import logging
import httpx
from openai import AsyncOpenAI

logger = logging.getLogger("LobeFrontal.OpenAIInterface")

# Valeurs de reasoning_effort supportées par l'app (convention OpenAI/Groq).
# llama.cpp n'expose aucune liste de valeurs supportées via son API — le
# paramètre reasoning_effort y est un passthrough non validé (voir
# parse_model_metadata) — donc ce sont ces valeurs par défaut qui sont
# utilisées tant qu'aucun serveur ne les annonce explicitement.
_STANDARD_REASONING_EFFORTS = ["none", "low", "medium", "high"]


def _match_or_single(entries: list, model_id: str) -> dict | None:
    """
    Cherche entries[i]["id"] == model_id ; si rien ne matche mais qu'une
    seule entrée est présente, la retourne quand même. llama.cpp sert un
    seul modèle par instance sous un id souvent imprévisible (chemin de
    fichier) qui ne correspond pas forcément à celui configuré côté app.

    Ignore les entrées qui ne sont pas des dict (forme de réponse
    inattendue) plutôt que de planter sur .get().
    """
    entries = [e for e in entries if isinstance(e, dict)] if entries else []
    match = next((e for e in entries if e.get("id") == model_id), None)
    if match is None and len(entries) == 1:
        match = entries[0]
    return match


def parse_model_metadata(model_id: str, sdk_models: list[dict], raw_models_response: dict | None) -> dict:
    """
    Combine les métadonnées SDK (models.list()) et la réponse HTTP brute de
    /v1/models en un dict de détails de modèle. Fonction pure — pas d'I/O,
    testable sans client HTTP ni event loop.
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

    model_obj = _match_or_single(sdk_models, model_id)
    if model_obj:
        details["object"] = model_obj.get("object")
        details["created"] = model_obj.get("created")
        details["owned_by"] = model_obj.get("owned_by")

    raw_entries = raw_models_response.get("data") if isinstance(raw_models_response, dict) else None
    raw = _match_or_single(raw_entries or [], model_id)

    if raw:
        details["raw_server_metadata"] = raw

        context_length = None
        for key in ("context_length", "max_model_len", "max_seq_len", "context_window"):
            if key in raw:
                context_length = raw[key]
                break
        if context_length is None:
            # Forme réelle de llama.cpp : la taille de contexte chargée vit
            # sous meta.n_ctx, pas à la racine de l'entrée.
            context_length = (raw.get("meta") or {}).get("n_ctx")
        details["context_length"] = context_length

        # Aucun serveur llama.cpp connu n'expose ces champs (vérifié en direct
        # contre un llama-server réel) — ce chemin ne sert donc que si un
        # backend OpenAI-compatible différent les annonce un jour.
        reasoning = raw.get("supported_reasoning_efforts") or raw.get("reasoning_efforts")
        if reasoning:
            details["supported_reasoning_efforts"] = reasoning
        elif isinstance(raw.get("capabilities"), dict) and raw["capabilities"].get("reasoning"):
            details["supported_reasoning_efforts"] = raw["capabilities"]["reasoning"]

        if isinstance(raw.get("capabilities"), dict):
            details["capabilities"] = raw["capabilities"]

    if details["supported_reasoning_efforts"]:
        details["_reasoning_efforts_source"] = "server"
    else:
        details["supported_reasoning_efforts"] = list(_STANDARD_REASONING_EFFORTS)
        details["_reasoning_efforts_source"] = "fallback_standard"

    return details


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
        Récupère les informations détaillées d'un modèle (context_length,
        capabilities, reasoning efforts) en combinant le SDK et un appel HTTP
        brut à /v1/models, puis délègue le parsing à parse_model_metadata().
        """
        sdk_models = []
        try:
            models_list = await self.openai_client.models.list()
            sdk_models = [m.model_dump() for m in models_list.data]
        except Exception as e:
            logger.warning(f"SDK models.list échoué: {e}")

        raw_models_response = None
        try:
            # self.openai_client._client (httpx brut) n'a pas de base_url —
            # AsyncOpenAI la gère en interne — donc il faut une URL absolue,
            # sinon httpx lève "Request URL is missing an 'http://' protocol".
            url = self.openai_client.base_url.join("models")
            response = await self.openai_client._client.get(str(url))
            if response.status_code == 200:
                raw_models_response = response.json()
            else:
                logger.warning(f"HTTP /v1/models → {response.status_code}")
        except Exception as e:
            logger.warning(f"Appel HTTP brut échoué: {e}")

        details = parse_model_metadata(model_id, sdk_models, raw_models_response)

        logger.info(f"📋 Détails du modèle '{model_id}': context={details['context_length']}, "
                     f"reasoning_efforts={details['supported_reasoning_efforts']} "
                     f"(source: {details['_reasoning_efforts_source']})")

        return details

    async def get_completion(self, messages: list, model, temperature, top_p) -> str:
        """Appel non-streaming, sans tools, pour une réflexion silencieuse ponctuelle
        (ex: lobe.topic.generate, #15) — jamais lié à lobe.fragment_stream."""
        response = await self.openai_client.chat.completions.create(
            messages=messages,
            model=model,
            stream=False,
            temperature=temperature,
            top_p=top_p,
        )
        return (response.choices[0].message.content or "").strip()

    async def get_stream(self, messages: list, model, tools, temperature, top_p, reasoning_effort):
        logger.info("Envoi immédiat de la requête LLM...")
        # reasoning_effort est ignoré par llama.cpp (vérifié en direct) ; le
        # vrai interrupteur pour ce backend est chat_template_kwargs.enable_thinking,
        # qu'on dérive donc nous-mêmes de reasoning_effort.
        enable_thinking = str(reasoning_effort).strip().lower() != "none"
        return await self.openai_client.chat.completions.create(
            messages=messages,
            model=model,
            tools=tools if tools else None,  # Évite d'envoyer un tableau vide inutile
            stream=True,
            temperature=temperature,
            top_p=top_p,
            reasoning_effort=reasoning_effort,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}}
        )


def _self_check():
    # Fixture "well-formed" = capture réelle d'un /v1/models llama.cpp (Gemma-4).
    real_llamacpp_response = {
        "object": "list",
        "data": [
            {
                "id": "/mnt/forge/ai/models/Gemma-4/Gemma-4-E2B/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
                "object": "model",
                "created": 1784318281,
                "owned_by": "llamacpp",
                "meta": {"n_ctx": 20224, "n_ctx_train": 131072},
            }
        ],
    }
    model_id = real_llamacpp_response["data"][0]["id"]
    sdk_single = [{"id": model_id, "object": "model", "created": 1784318281, "owned_by": "llamacpp"}]

    # 1. Réponse bien formée, mais sans champ reasoning-efforts (cas réel
    #    llama.cpp) -> context_length lu depuis meta.n_ctx, fallback pour
    #    les reasoning efforts.
    d = parse_model_metadata(model_id, sdk_single, real_llamacpp_response)
    assert d["context_length"] == 20224
    assert d["supported_reasoning_efforts"] == _STANDARD_REASONING_EFFORTS
    assert d["_reasoning_efforts_source"] == "fallback_standard"
    assert d["owned_by"] == "llamacpp"

    # 2. model_id qui ne matche aucune entrée mais un seul modèle servi
    #    (cas réel: .env LLM_MODEL != chemin de fichier llama.cpp) -> on
    #    utilise quand même l'unique entrée, côté SDK comme côté HTTP brut.
    d = parse_model_metadata("some-other-alias", sdk_single, real_llamacpp_response)
    assert d["context_length"] == 20224
    assert d["owned_by"] == "llamacpp"

    # 3. Serveur qui annonce vraiment des reasoning efforts (ex. vLLM) ->
    #    source doit être "server", pas le fallback.
    server_reports_efforts = {
        "data": [{"id": model_id, "context_length": 8192, "supported_reasoning_efforts": ["low", "high"]}]
    }
    d = parse_model_metadata(model_id, [], server_reports_efforts)
    assert d["context_length"] == 8192
    assert d["supported_reasoning_efforts"] == ["low", "high"]
    assert d["_reasoning_efforts_source"] == "server"

    # 4. Forme de réponse inattendue/vide -> pas de crash, tout retombe sur
    #    les défauts/fallback. Inclut des formes non-dict (liste au lieu
    #    d'objet, entrées non-dict) qui plantaient auparavant sur .get().
    unexpected_shapes = (
        None,
        {},
        {"data": []},
        {"data": [{"id": "unrelated"}, {"id": "also-unrelated"}]},
        [{"id": model_id}],
        {"data": ["not-a-dict", 42]},
    )
    for shape in unexpected_shapes:
        d = parse_model_metadata(model_id, [], shape)
        assert d["id"] == model_id
        assert d["context_length"] is None
        assert d["supported_reasoning_efforts"] == _STANDARD_REASONING_EFFORTS
        assert d["_reasoning_efforts_source"] == "fallback_standard"

    print("OK: parse_model_metadata self-check passed")


if __name__ == "__main__":
    _self_check()
