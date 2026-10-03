"""Couture unique du banc d'eval : tout ce qu'on sait d'un run se dérive du flux.

Métriques de vitesse et contrôles de qualité lisent la même source — les chunks
streamés — donc ils vivent dans la même fonction, en amont de tout I/O. Le test
injecte de faux chunks et une horloge fictive : ni llama.cpp, ni réseau.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fragments import take_fragment

# Balises du prompt système (cf. PromptBuilder.build_system_prompt) : si l'une
# ressort dans la réponse, le modèle recrache son prompt au lieu de jouer Aletheia.
XML_LEAK_PATTERN = re.compile(
    r"</?\s*(system|persona|core_memory|users|tools?|mood|vision|recall|context)\b", re.I
)

# ponytail: détection de langue par mots-outils, pas de dépendance langdetect.
# Suffisant pour trancher « le modèle a répondu en anglais » sur des réponses de
# plusieurs phrases ; si un jour il faut arbitrer du français/espagnol sur trois
# mots, prendre une vraie lib.
_FR_WORDS = {
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "le", "la", "les",
    "un", "une", "des", "du", "de", "et", "est", "que", "qui", "pas", "pour",
    "dans", "ce", "cette", "mais", "avec", "plus", "tout", "bien", "ça", "moi",
    "toi", "suis", "es", "sais", "veux", "peux", "alors", "donc", "quoi",
}
_EN_WORDS = {
    "i", "you", "he", "she", "it", "we", "they", "the", "a", "an", "of", "and",
    "is", "are", "that", "not", "for", "in", "this", "but", "with", "more",
    "all", "well", "me", "am", "know", "want", "can", "so", "what", "your",
}
_WORD_PATTERN = re.compile(r"[a-zà-ÿ']+", re.I)


def is_french(text: str) -> bool:
    words = [w.lower() for w in _WORD_PATTERN.findall(text)]
    fr = sum(1 for w in words if w in _FR_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    return fr > en


async def consume_stream(stream, start: float, clock=time.monotonic) -> dict:
    """Consomme un flux de chunks OpenAI et retourne tout ce que le banc mesure.

    `start` est l'instant (même horloge que `clock`) où la requête est partie.
    La segmentation en fragments reproduit main.py à l'identique — une seule
    recherche de ponctuation par token, puis flush du reste en fin de flux —
    pour que le TTFF mesuré soit celui que io_voix verrait réellement.
    """
    text = ""
    buffer = ""
    fragments = []
    tool_calls: dict[int, dict] = {}
    finish_reason = None
    usage = None
    ttft = None
    ttff = None
    first_token_at = None
    last_token_at = None
    content_chunks = 0

    async for chunk in stream:
        now = clock()
        usage = getattr(chunk, "usage", None) or usage
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)

        token = getattr(delta, "content", None)
        if token:
            if first_token_at is None:
                first_token_at = now
                ttft = now - start
            last_token_at = now
            content_chunks += 1
            text += token
            buffer += token

            fragment, buffer = take_fragment(buffer)
            if fragment:
                fragments.append(fragment)
                if ttff is None:
                    ttff = now - start

        for tc_chunk in getattr(delta, "tool_calls", None) or []:
            idx = getattr(tc_chunk, "index", 0)
            fn = getattr(tc_chunk, "function", None)
            entry = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": []})
            if getattr(tc_chunk, "id", None):
                entry["id"] = tc_chunk.id
            if getattr(fn, "name", None):
                entry["name"] += fn.name
            if getattr(fn, "arguments", None):
                entry["arguments"].append(fn.arguments)

        if getattr(choices[0], "finish_reason", None):
            finish_reason = choices[0].finish_reason

    end = clock()

    # Flush final, comme main.py : une réponse sans aucune ponctuation ne
    # déclenche la voix qu'ici, et son TTFF est donc la fin du flux.
    # Sauf sur un tour d'outil : main.py y garde la queue pour le tour suivant
    # au lieu de la publier, donc la flusher ici fabriquerait un TTFF que la
    # production ne produit jamais. Le banc ne joue qu'un tour — sur ce chemin
    # il n'y a pas de TTFF à rapporter, et None se lit mieux qu'un faux chiffre.
    tail = buffer.strip()
    if tail and finish_reason != "tool_calls":
        fragments.append(tail)
        if ttff is None:
            ttff = end - start

    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    if completion_tokens is None:
        completion_tokens = content_chunks
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None

    decode_duration = (last_token_at - first_token_at) if first_token_at is not None else 0.0
    decode_tps = completion_tokens / decode_duration if decode_duration > 0 else None
    prefill_tps = prompt_tokens / ttft if prompt_tokens and ttft else None

    return {
        "ttft": ttft,
        "ttff": ttff,
        "total": end - start,
        "decode_tps": decode_tps,
        "prefill_tps": prefill_tps,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "text": text,
        "fragments": fragments,
        "tool_calls": [
            {"id": tc["id"], "name": tc["name"], "arguments": "".join(tc["arguments"])}
            for _, tc in sorted(tool_calls.items())
        ],
        "finish_reason": finish_reason,
        "french": is_french(text),
        "xml_leak": bool(XML_LEAK_PATTERN.search(text)),
    }
