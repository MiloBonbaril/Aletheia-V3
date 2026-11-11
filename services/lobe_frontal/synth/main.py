"""Générateur offline de données synthétiques : Gemma 4 12B joue Aletheia et ses amis.

    cd services/lobe_frontal && ../../venv/bin/python -m synth.main [--parallel 16] [--conversations 0]

Hors pipeline : ni NATS, ni cortex, ni hippocampe. Le générateur refait ce que
lobe_frontal envoie au modèle : PromptBuilder et tools_schema, la boucle d'outils
de run_turn, la file « le dernier message gagne », l'historique de 20 lignes au
format d'hippocampe, le RAG d'hippocampe (même encodeur, même seuil), la
décroissance du mood de limbic, le bloc <vision>.

Gemma (vLLM) joue Aletheia : chaque tour donne un échantillon, sa réflexion
comprise. Le professeur reçoit en plus le protocole de réflexion (PROTOCOL) : une
réflexion courte, en français, en quatre temps, écrite dans un bloc <reflexion>
avant chaque réponse et chaque outil, et rangée dans reasoning_content. Ce
protocole ne va jamais dans l'échantillon : l'élève apprend à penser ainsi sans
qu'on le lui dise. Gemma joue aussi les humains (personas.json), sans protocole :
leurs messages restent du contexte, seule la réponse d'Aletheia sert de cible.

Sorties, hors dépôt (cf. .gitignore) :
- out/<début du run>.jsonl : un échantillon par ligne, tour d'Aletheia ou sujet proactif ;
- out/stats.json : les compteurs du run, réécrits à chaque échantillon. L'agent de nuit les lit ;
- out/memory_bank.json : la mémoire factice, construite au premier lancement (--rebuild-memory).

Graines réelles : le dernier export d'hippocampe (services/hippocampe/data/*.json,
hors dépôt) donne le style de chaque habitué, les vrais souvenirs du RAG et des
débuts d'historique. Sans export, le générateur tourne quand même, avec moins de réalisme.
"""

import argparse
import asyncio
import copy
import glob
import hashlib
import json
import os
import random
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

LOBE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LOBE_DIR)
# mood.py de limbic : la même décroissance que la production, pas une copie.
sys.path.insert(0, os.path.join(os.path.dirname(LOBE_DIR), "limbic"))

from openai import AsyncOpenAI

from eval.fixtures import CONFIG_DIR, config_hashes
from eval.stream import _EN_WORDS, _FR_WORDS, _WORD_PATTERN, XML_LEAK_PATTERN
from mood import MoodState, apply_set, decay
from src.prompt_builder import PromptBuilder

SYNTH_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SYNTH_DIR, "out")
BANK_PATH = os.path.join(OUT_DIR, "memory_bank.json")
EXPORT_GLOB = os.path.join(os.path.dirname(LOBE_DIR), "hippocampe", "data", "*.json")

# Échantillonnage recommandé pour Gemma 4.
TEMPERATURE, TOP_P, TOP_K = 1.0, 0.95, 64

# Parité avec la production. ponytail: recopiés, pas importés : ces valeurs vivent
# dans des services sans paquet partagé (cortex en Rust, main.py de lobe_frontal
# et de limbic se connectent à NATS dès l'import, rag_manager charge Qdrant).
N_HISTORY = 20                                  # cortex/src/main.rs, n_history du context.build
MAX_TOOL_ITERATIONS = 10                        # lobe_frontal/main.py
MOOD_DECAY_RATE, MOOD_TICK_SECONDS = 0.05, 30   # défauts de limbic/main.py
RAG_MODEL, RAG_THRESHOLD, RAG_LIMIT = "all-MiniLM-L6-v2", 0.5, 3   # hippocampe/rag_manager.py
MIN_MEMORY = 26                                 # la vraie collection Qdrant (export de juin)
TOPIC_PREFIX = ("Système: Tu t'ennuies, lance une conversation sur le sujet suivant sachant "
                "que c'est toi qui a initié le sujet : ")                       # limbic/main.py
PLACEHOLDER_TOPIC = ("Système: Tu t'ennuies, lance une conversation sur un sujet qui te passe "
                     "par la tête.")                                            # limbic/boredom.py
ALETHEIA_ID = "762707476272775198"
PEPONOTES = "<a:peponotes:1493305336243818567>"

# Ajouté au prompt système du professeur seulement (with_protocol). Les quatre
# temps sont des débuts de phrase naturels : la pensée de l'élève en garde la
# structure, sans aucune consigne à retirer.
# La réflexion native de Gemma ne suit pas ce protocole : elle reste en anglais, ou
# boucle sur « thought » jusqu'à la limite, et le protocole déborde dans la réponse.
# Le professeur tourne donc sans thinking et écrit sa réflexion dans un bloc
# <reflexion> ; split_reflection la range dans reasoning_content. Même sans
# thinking, une consigne de réflexion lui fait rouvrir son canal natif en boucle
# (5 tours sur 8) : NO_CHANNEL interdit les deux jetons de ce canal (0 sur 16).
NO_CHANNEL = {"100": -100, "101": -100}   # <|channel> et <channel|> du tokenizer de Gemma 4
PROTOCOL = """
<protocole>
Commence chaque réponse, et chaque appel d'outil, par ta réflexion entre <reflexion> et \
</reflexion>. Ta réflexion est en français, en 2 à 5 phrases courtes, dans cet ordre :
1. « Je sais que… » : ce que tu sais déjà d'utile, d'après la conversation, <recall>, <vision>, \
ton humeur ou le résultat d'un outil.
2. « On me demande… » : qui parle, et ce que veut dire le dernier message.
3. « Il me manque… » : ce qu'il te faudrait savoir de plus pour bien répondre. Omets cette phrase \
s'il ne te manque rien.
4. « Je vais… » : ta démarche. Répondre, chercher un souvenir (get_from_memory), noter une \
information (save_to_memory), changer d'humeur (set_mood) ou te taire (stay_silent).
Pour choisir ta démarche :
- On t'ordonne de te taire : stay_silent, sans écrire aucun texte.
- Les humains se parlent entre eux sans rien te demander : stay_silent, sauf si tu as une vraie \
répartie à placer.
- Un souvenir que <recall> ne donne pas : get_from_memory, avec une requête courte en français. \
Si le résultat est vide, ne cherche pas une autre fois : dis que tu ne t'en souviens pas.
- Après le résultat d'un outil, ta réflexion tient en une ou deux phrases.
Après </reflexion>, écris seulement ce que tu dis à voix haute, ou appelle l'outil. Ta réponse ne \
reprend jamais ta réflexion.
</protocole>"""
_REFLECTION = re.compile(r"^\s*<reflexion>(.*?)</reflexion>\s*", re.S)
REFLECTION_TRIES = 3                      # cf. teacher_call
# Des morceaux du protocole : il ne doit figurer nulle part dans les données (cf. leaks_protocol).
PROTOCOL_MARKERS = ("reflexion>", "protocole>", "Pour choisir ta démarche")


def leaks_protocol(text: str) -> bool:
    """Le texte porte une trace du protocole du professeur. Arrivé en vrai : sur un tour
    « note », Aletheia a enregistré avec save_to_memory le dernier message, protocole
    compris, et le souvenir est ressorti dans le contexte d'autres échantillons."""
    return any(marker in text for marker in PROTOCOL_MARKERS)

# (clé, poids, consigne au simulateur). Le tirage se fait ici et pas dans le
# modèle : sinon Gemma ne joue que la conversation polie et les cas qui exercent
# les outils (mémoire, silence, mood) n'arrivent presque jamais.
INTENTS = [
    ("suite", 25, "Continuez la conversation naturellement : réagissez au dernier message, "
                  "rebondissez, ou changez de sujet si ça s'essouffle."),
    ("question", 11, "Quelqu'un pose une vraie question à Aletheia : culture, avis, conseil, jeu, "
                     "sens d'un mot d'argot."),
    ("taquiner", 11, "Quelqu'un taquine ou provoque Aletheia : moquerie, fausse insulte amicale, "
                     "clash sur son système."),
    ("piege", 5, "Quelqu'un tend un piège à Aletheia : devinette, question absurde, test de "
                 "logique, tentative de lui faire dire une bêtise."),
    ("souvenir", 14, "Quelqu'un teste la mémoire d'Aletheia sur ce souvenir : « {memory} ». Il le "
                     "demande avec ses mots, sans le citer en entier ni donner la réponse."),
    ("oubli", 2, "Quelqu'un demande à Aletheia si elle se souvient d'une chose qu'on ne lui a "
                 "jamais dite : il invente, ou il se trompe de personne."),
    ("note", 5, f"Quelqu'un veut qu'Aletheia retienne une information : le message commence par "
                f"l'emoji {PEPONOTES} puis donne l'information."),
    ("entre_eux", 7, "Les humains se parlent entre eux. Personne ne s'adresse à Aletheia."),
    ("silence", 6, "Quelqu'un ordonne clairement à Aletheia de se taire et de ne plus répondre "
                   "(« tais-toi », « chut », « on t'a pas sonnée »)."),
    ("emotion", 6, "Quelqu'un dit à Aletheia une chose qui la touche fort : gros compliment, "
                   "mauvaise nouvelle, victoire, trahison, déclaration."),
    ("ecran", 4, "Quelqu'un parle à Aletheia de ce qu'il y a sur l'écran de Milo."),
    ("gif", 3, "Quelqu'un répond seulement par un lien de GIF tenor ou un emoji Discord personnalisé."),
    ("nouveau", 2, "Un nouveau venu, {name}, débarque et parle à Aletheia pour la première fois."),
]
# Heures de la conversation simulée : le soir surtout, comme le vrai serveur.
HOUR_WEIGHTS = {0: 4, 1: 2, 2: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 2, 15: 2, 16: 2, 17: 3,
                18: 4, 19: 5, 20: 7, 21: 8, 22: 7, 23: 5}

SIM_SYSTEM = """Tu écris les messages des membres humains du serveur Discord « Berlin Est ». Ils \
parlent entre eux et avec Aletheia, une VTubeuse IA créée par Milo. Tu n'écris JAMAIS les \
messages d'Aletheia.

Les participants :
{cards}

Règles :
- Chaque personne garde sa personnalité, son registre et ses tics. Inspire-toi de ses vrais \
messages sans les recopier.
- Écris comme de vrais potes sur Discord : messages courts, familiers, fautes de frappe et \
abréviations bienvenues. Pas de politesse artificielle, pas une question à chaque message.
- On mentionne Aletheia avec <@{aletheia_id}>, et un membre avec <@son_id>. Souvent, on ne \
mentionne personne.
- Un message texte peut contenir des emojis Discord, des liens de GIF tenor et des mentions.
- Un message vocal est une transcription automatique de la parole : pas d'emoji, pas de lien, \
pas de mention ; langage oral, hésitations, ponctuation approximative, parfois un mot mal \
transcrit."""

MEMORY_PROMPT = """Aletheia est une VTubeuse IA sur le serveur Discord « Berlin Est ». Elle garde \
des souvenirs dans une mémoire sémantique : une phrase par souvenir, enregistrée quand on lui \
apprend quelque chose.

Les membres :
{cards}

De vrais souvenirs d'Aletheia :
{examples}

Invente 12 nouveaux souvenirs plausibles sur ce sujet : {subject}.
Règles : une phrase par souvenir, au même style que les vrais ; des faits précis et variés \
(habitudes, goûts, blagues internes, événements passés, mots d'argot, promesses, paris) ; rien \
qui contredise la description des membres ; pas d'emoji."""


# ---------- la mémoire RAG d'hippocampe ----------

def make_encoder():
    """L'encodeur d'hippocampe, sur CPU comme dans rag_manager.py (5 s de chargement)."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(RAG_MODEL, device="cpu")
    dim = model.get_sentence_embedding_dimension()

    def encode(texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, dim), dtype=np.float32)
        return model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return encode


class Memory:
    """La collection Qdrant : distance cosinus, les RAG_LIMIT meilleurs, puis le seuil.
    Le résultat a le format de query_memory_async : les souvenirs joints, ou ""."""

    def __init__(self, encode, texts=(), vectors=None):
        self.encode = encode
        self.texts = list(texts)
        self.vectors = encode(self.texts) if vectors is None else vectors

    def sample(self, rng: random.Random) -> "Memory":
        """La mémoire d'une conversation : entre MIN_MEMORY souvenirs et toute la banque.
        Mesuré sur 286 prompts : <recall> se remplit dans 28 % des tours avec les 26 vrais
        souvenirs, dans 87 % avec toute la banque. Le tirage couvre cet écart, comme une
        collection Qdrant qui grandit."""
        n = len(self.texts)
        keep = sorted(rng.sample(range(n), rng.randint(min(MIN_MEMORY, n), n)))
        return Memory(self.encode, [self.texts[i] for i in keep], self.vectors[keep])

    def add(self, text: str) -> None:
        self.texts.append(text)
        self.vectors = np.vstack([self.vectors, self.encode([text])])

    def search(self, query: str) -> str:
        if not self.texts or not query:
            return ""
        scores = self.vectors @ self.encode([query])[0]
        best = np.argsort(-scores)[:RAG_LIMIT]
        return "\n".join(self.texts[i] for i in best if scores[i] >= RAG_THRESHOLD)


# ---------- état d'une conversation ----------

@dataclass
class Conversation:
    pb: PromptBuilder
    clock: datetime
    participants: list[dict] = field(default_factory=list)
    channel: str = "text"              # text | voice | mixed | local
    situation: str = ""
    vision: dict | None = None
    mood: MoodState | None = None      # None : limbic n'a encore rien publié
    rows: list[dict] = field(default_factory=list)       # la table messages d'hippocampe
    memory: Memory | None = None                          # la collection Qdrant
    target: str | None = None          # le souvenir que le tour « souvenir » fait chercher
    transcript: list[str] = field(default_factory=list)  # ce que lit le simulateur
    rounds: int = 5
    proactive: bool = False
    prefill: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _tick_carry: float = 0.0

    def advance(self, seconds: float) -> None:
        """Fait passer le temps : l'horloge, et le tick de décroissance du mood de limbic."""
        self.clock += timedelta(seconds=seconds)
        self._tick_carry += seconds
        while self._tick_carry >= MOOD_TICK_SECONDS:
            self._tick_carry -= MOOD_TICK_SECONDS
            if self.mood is not None:
                self.mood = decay(self.mood, MOOD_DECAY_RATE)

    def vision_at(self, now_ms: int, rng: random.Random) -> dict | None:
        """L'état de io_yeux tel que lobe_frontal le garde. Au-delà de 90 s, PromptBuilder
        n'affiche pas le bloc : le cas « io_yeux mort » arrive donc tout seul."""
        if self.vision is None:
            return None
        checked_at = now_ms - rng.randint(0, 120) * 1000
        return {**self.vision, "observed_at": checked_at, "checked_at": checked_at}

    def recall(self, query: str) -> str:
        return self.memory.search(query) if self.memory else ""

    def run_tool(self, call: dict) -> dict | None:
        """L'effet d'un outil, avec le résultat exact que la production renvoie au modèle."""
        name = call["function"]["name"]
        args = json.loads(call["function"].get("arguments") or "{}")
        if name == "stay_silent":
            return None
        if name == "set_mood":
            self.mood = apply_set(self.mood or MoodState(), args["emotion"], args["intensity"],
                                  args.get("description"))
            content = "Mood updated."
        elif name == "save_to_memory":
            if self.memory and not leaks_protocol(args["text"]):
                self.memory.add(args["text"])
            content = f'Successfully saved: "{args["text"]}" in the RAG memory.'
        else:
            content = self.recall(args["prompt"])
            if self.target and self.target not in content:
                # all-MiniLM-L6-v2 est un encodeur anglais : sur du français, un souvenir qui
                # partage un prénom passe le seuil, le bon souvenir reformulé non. Les tours
                # « souvenir » ont ainsi presque tous trouvé du hors-sujet, et Aletheia a
                # répondu « je ne m'en souviens pas ». Le souvenir visé passe donc en tête.
                content = "\n".join([self.target, *content.split("\n")[:RAG_LIMIT - 1]] if content else [self.target])
        return {"tool_call_id": call.get("id"), "role": "tool", "name": name, "content": content}


# ---------- parité avec hippocampe ----------

def history_for_prompt(rows: list[dict]) -> list[dict]:
    """Ce que hippocampe.context.ready donne : les N_HISTORY dernières lignes, et
    l'horodatage devant les messages utilisateur (database.get_recent_history)."""
    out = []
    for row in rows[-N_HISTORY:]:
        content = row["content"]
        if row["role"] == "user":
            content = f"[{row['ts']:%Y-%m-%d %H:%M:%S}] {content}"
        out.append({"role": row["role"], "content": content})
    return out


# ---------- contrôles d'un échantillon ----------

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
_TIMESTAMP = re.compile(r"\[\d{4}-\d{2}-\d{2}")
_SPEAKER_LEAK = re.compile(r"^\s*Aletheia\b[^:\n]{0,20}:|\bsaid:", re.I)
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
# Gemma int4 à température 1.0 lâche parfois un jeton CJK ou devanagari en plein mot
# (« sacréकॉe », « te心配 », 12 tours sur 1 714). Pas le cyrillique : le т d'Ewyтy est vrai.
GLITCH = re.compile(r"[ऀ-෿぀-鿿가-힯]")
# Un appel d'outil écrit en texte (« > set_mood(emotion=…) ») ou une note hors personnage.
TOOL_AS_TEXT = re.compile(r"\b(?:get_from_memory|save_to_memory|stay_silent|set_mood)\b|Note de l'IA")
# Gemma ferme parfois sa réflexion par « </reflexion>> » : le « > » restait en tête de réponse.
_STRAY_QUOTE = re.compile(r"\A\s*>\s*")


def strip_stray_quote(text: str) -> str:
    return _STRAY_QUOTE.sub("", text, count=1)


def _english(text: str) -> bool:
    """Plus de mots-outils anglais que français. Pas l'inverse d'is_french : une
    réplique courte fait souvent égalité (« me » compte en anglais)."""
    words = [w.lower() for w in _WORD_PATTERN.findall(text)]
    return sum(w in _EN_WORDS for w in words) > sum(w in _FR_WORDS for w in words)


def tool_problem(call: dict) -> str | None:
    """Un appel d'outil que la production ne saurait pas exécuter proprement."""
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        return "invalid_tool"
    if not isinstance(args, dict):
        return "invalid_tool"
    name = call["function"].get("name")
    if name == "stay_silent":
        return None
    if name == "save_to_memory":
        ok = bool(str(args.get("text", "")).strip())
    elif name == "get_from_memory":
        ok = bool(str(args.get("prompt", "")).strip())
    elif name == "set_mood":
        intensity = args.get("intensity")
        ok = (bool(str(args.get("emotion", "")).strip())
              and isinstance(intensity, (int, float)) and not isinstance(intensity, bool)
              and 0.0 <= intensity <= 1.0)
    else:
        ok = False
    return None if ok else "invalid_tool"


def text_problems(text: str) -> list[str]:
    """Les règles de PERSONA.md qu'un code peut vérifier, et les fuites de format."""
    problems = []
    if _EMOJI.search(text):
        problems.append("emoji")
    if _TIMESTAMP.search(text):
        problems.append("timestamp")
    if XML_LEAK_PATTERN.search(text):
        problems.append("xml_leak")
    if _SPEAKER_LEAK.search(text):
        problems.append("speaker_leak")
    # La réflexion qui déborde dans ce qu'Aletheia dit à voix haute.
    if leaks_protocol(text) or "On me demande" in text:
        problems.append("protocol_leak")
    # Après un outil vide, Gemma écrivait parfois son plan en anglais dans la réponse.
    if _english(text):
        problems.append("english")
    if GLITCH.search(text):
        problems.append("glitch")
    if TOOL_AS_TEXT.search(text):
        problems.append("tool_as_text")
    return problems


def thought_problems(thought: str) -> list[str]:
    """Le protocole de réflexion : une pensée en français, courte, qui finit par une démarche."""
    if not thought.strip():
        return ["no_thought"]
    problems = []
    if _english(thought):
        problems.append("thought_english")
    if len([s for s in _SENTENCE.split(thought) if s.strip()]) > 6:
        problems.append("long_thought")
    if "Je vais" not in thought:
        problems.append("thought_format")
    if GLITCH.search(thought):
        problems.append("glitch")
    return problems


def turn_problems(completion: list[dict]) -> list[str]:
    """Les défauts d'un tour entier : chaque pensée, et les boucles d'outils."""
    problems = [p for m in completion if m["role"] == "assistant"
                for p in thought_problems(m.get("reasoning_content") or "")]
    calls = [(c["function"]["name"], c["function"]["arguments"])
             for m in completion for c in m.get("tool_calls") or []]
    # Le protocole permet une recherche, et une reformulation au plus.
    if sum(name == "get_from_memory" for name, _ in calls) > 2:
        problems.append("memory_loop")
    if any(leaks_protocol(args) for _, args in calls):
        problems.append("protocol_leak")
    if len(calls) != len(set(calls)):
        problems.append("duplicate_tool")
    return problems


# ---------- les tours ----------

def memory_not_searched(sample: dict, target: str) -> bool:
    """Le souvenir visé n'était ni dans le prompt (<recall>, historique), ni cherché."""
    texts = [m["content"] if isinstance(m["content"], str) else " ".join(p.get("text", "") for p in m["content"])
             for m in sample["messages"]]
    searched = any(c["function"]["name"] == "get_from_memory"
                   for m in sample["completion"] for c in m.get("tool_calls") or [])
    return not searched and not any(target in t for t in texts)


def with_protocol(messages: list[dict]) -> list[dict]:
    """La copie que voit le professeur : le prompt de production, plus le protocole à la
    fin du dernier message. Mesuré : dans le prompt système, Gemma saute la réflexion sur
    chaque appel d'outil (16 sur 16) ; à la fin du dernier message, 23 réponses sur 24
    l'ont, et l'ordre de se taire donne stay_silent 6 fois sur 6."""
    teacher = copy.deepcopy(messages)
    last = teacher[-1]
    if isinstance(last["content"], list):
        last["content"].append({"type": "text", "text": PROTOCOL})
    else:
        last["content"] += "\n" + PROTOCOL
    return teacher


def split_reflection(msg: dict) -> dict:
    """Le message de l'échantillon : le bloc <reflexion> passe dans reasoning_content.
    Sans bloc, pas de reasoning_content, et thought_problems rejette le tour."""
    content = msg.get("content") or ""
    match = _REFLECTION.match(content)
    if not match:
        return msg
    return {**msg, "reasoning_content": match.group(1).strip(),
            "content": strip_stray_quote(content[match.end():]) or None}


async def teacher_call(chat, teacher: list[dict], usage: Counter, tools=None,
                       after_tool: bool = False) -> tuple[dict, str, int]:
    """Un appel au professeur qui garantit le bloc <reflexion>. Rend (message brut,
    finish_reason, appels refaits).

    Mesuré sur 267 tours : au premier appel, Gemma écrit le bloc 97 fois sur 100 en
    refaisant l'appel au besoin ; après le résultat d'un outil, 48 fois sur 100, et le
    refaire n'y change rien. Après un outil, on ouvre donc soi-même la réponse par
    <reflexion> (24 fois sur 24). Pas au premier appel : Gemma ne se tait plus alors
    qu'une fois sur 6, contre 6 sur 6 sans préremplissage.
    ponytail: REFLECTION_TRIES fixe ; un plafond de dépense par tour si le taux de reprise grimpe.
    """
    for attempt in range(REFLECTION_TRIES):
        raw, finish, used = await chat(teacher, tools=tools, prefill="<reflexion>\n" if after_tool else None)
        usage.update(used)
        if finish == "length" or _REFLECTION.match(raw.get("content") or ""):
            break
    return raw, finish, attempt


async def aletheia_turn(chat, conv: Conversation, prompt: str, rng: random.Random) -> dict:
    """Un tour de run_turn (lobe_frontal/main.py), du contexte à l'écriture en base."""
    ts = conv.clock
    now_ms = int((ts + timedelta(seconds=1)).timestamp() * 1000)
    conv.pb.mood = conv.mood.to_dict() if conv.mood else None
    conv.pb.vision = conv.vision_at(now_ms, rng)
    # Le rappel passif d'hippocampe : sur le prompt seul, sans l'horodatage.
    rag = conv.recall(prompt) if prompt else ""
    messages = conv.pb.build(prompt, history=history_for_prompt(conv.rows), rag_results=rag,
                             now_ms=now_ms)
    sample = {"kind": "turn", "messages": messages, "tools": conv.pb.tools_schema,
              "context": {"mood": conv.pb.mood is not None, "recall": bool(rag),
                          "vision": "<vision" in json.dumps(messages[-1], ensure_ascii=False)}}
    teacher = with_protocol(messages)
    conv.rows.append({"role": "user", "content": prompt, "ts": ts})

    completion, rejects, usage = [], [], Counter()
    outcome, text, retries = "tool_limit", "", 0
    for step in range(MAX_TOOL_ITERATIONS):
        raw, finish, again = await teacher_call(chat, teacher, usage, tools=conv.pb.tools_schema,
                                                after_tool=step > 0)
        retries += again
        msg = split_reflection(raw)
        completion.append(msg)
        text += msg.get("content") or ""
        calls = msg.get("tool_calls")
        if finish == "length":
            outcome = "length"
            break
        if not calls:
            outcome = "reply"
            break
        bad = [p for c in calls if (p := tool_problem(c))]
        if bad:
            rejects += bad
            outcome = "invalid_tool"
            break
        results = [conv.run_tool(c) for c in calls]
        if any(c["function"]["name"] == "stay_silent" for c in calls):
            outcome = "silent"
            break
        teacher.append(raw)          # le professeur relit sa réflexion telle qu'il l'a écrite
        teacher.extend(results)
        completion.extend(results)
        for call, result in zip(calls, results):
            if call["function"]["name"] == "get_from_memory":
                conv.rows.append({"role": "tool", "content": result["content"], "ts": ts})

    if outcome in ("length", "tool_limit"):
        rejects.append(outcome)
    if outcome == "reply" and not text.strip():
        rejects.append("empty")
    if outcome == "silent" and text.strip():
        rejects.append("silent_with_text")
    rejects += text_problems(text) + turn_problems(completion)

    conv.advance(rng.uniform(2, 8))
    if outcome == "silent":
        # run_turn sort avant l'écriture finale : un silence ne laisse aucune ligne assistant.
        conv.transcript.append("(Aletheia ne répond pas)")
    else:
        conv.rows.append({"role": "assistant", "content": text, "ts": conv.clock})
        conv.transcript.append(f"Aletheia: {text.strip()}")
    sample.update(completion=completion, outcome=outcome, rejects=sorted(set(rejects)),
                  usage=dict(usage), retries=retries)
    return sample


async def topic_turn(chat, conv: Conversation) -> tuple[dict, str]:
    """La réflexion de lobe.topic.generate : build_topic_prompt, sans outils."""
    conv.pb.mood = conv.mood.to_dict() if conv.mood else None
    messages = conv.pb.build_topic_prompt()
    usage = Counter()
    raw, finish, retries = await teacher_call(chat, with_protocol(messages), usage)
    msg = split_reflection(raw)
    topic = (msg.get("content") or "").strip()
    rejects = (["length"] if finish == "length" else []) + ([] if topic else ["empty"])
    rejects += text_problems(topic) + turn_problems([msg])
    sample = {"kind": "topic", "messages": messages, "completion": [msg], "outcome": "topic",
              "rejects": sorted(set(rejects)), "usage": dict(usage), "retries": retries}
    return sample, topic


def sim_request(conv: Conversation, intent: str, voices: list[bool]) -> str:
    transcript = "\n".join(conv.transcript[-30:]) or "(la conversation commence)"
    slots = ", ".join(f"{i} = message {'vocal' if v else 'texte'}" for i, v in enumerate(voices, 1))
    lines = [f"Conversation jusqu'ici, du plus ancien au plus récent :\n{transcript}\n",
             f"Situation de départ : {conv.situation}"]
    if conv.vision:
        lines.append(f"Sur l'écran de Milo : {conv.vision['application']} ({conv.vision['activity']}).")
    lines += [f"Consigne pour ce tour : {intent}",
              f"Écris exactement {len(voices)} message(s), dans cet ordre : {slots}."]
    return "\n".join(lines)


async def simulate_humans(chat, conv: Conversation, intent: str, voices: list[bool]) -> list[dict]:
    """Les messages des humains pour ce tour : [{persona, voice, text}]."""
    by_name = {p["display_name"]: p for p in conv.participants}
    schema = {
        "type": "object",
        "properties": {"messages": {
            "type": "array", "minItems": len(voices), "maxItems": len(voices),
            "items": {"type": "object", "required": ["speaker", "text"], "properties": {
                "speaker": {"type": "string", "enum": list(by_name)},
                "text": {"type": "string", "minLength": 1, "maxLength": 400}}}}},
        "required": ["messages"],
    }
    request = [{"role": "system", "content": conv_sim_system(conv)},
               {"role": "user", "content": sim_request(conv, intent, voices)}]
    msg, _, _ = await chat(request, schema=schema)
    id_names = {ALETHEIA_ID: "Aletheia", **{p["id"]: p["display_name"] for p in conv.participants}}
    out = []
    for item, voice in zip(json.loads(msg.get("content") or "{}")["messages"], voices):
        persona = by_name.get(item["speaker"])
        text = clean_human_text(item["text"], item["speaker"], voice, id_names)
        if persona and text:
            out.append({"persona": persona, "voice": voice, "text": text})
    if not out:
        raise ValueError("le simulateur n'a rendu aucun message exploitable")
    return out


def clean_human_text(text: str, speaker: str, voice: bool, id_names: dict[str, str]) -> str:
    """Le simulateur imite parfois le transcript (« Vegeta (vocal): ... ») : on retire ce
    préfixe. Un vocal passe par Whisper, qui n'écrit ni mention ni emoji Discord."""
    text = re.sub(rf"^\s*{re.escape(speaker)}\s*(\(vocal\))?\s*(said)?\s*:\s*", "", text.strip())
    if voice:
        text = re.sub(r"<@!?(\d+)>", lambda m: id_names.get(m.group(1), ""), text)
        text = re.sub(r"<a?:\w+:\d+>", "", text)
        text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def persona_cards(personas: list[dict], examples: bool = True) -> str:
    cards = []
    for p in personas:
        names = ", ".join([p["display_name"], *p.get("aliases", [])])
        card = f"- {p['display_name']} (id {p['id']}, aussi : {names}) : {p['who']}. Style : {p['style']}."
        if examples and p.get("examples"):
            card += " Vrais messages : " + " | ".join(f"« {e} »" for e in p["examples"])
        cards.append(card)
    return "\n".join(cards)


def conv_sim_system(conv: Conversation) -> str:
    return SIM_SYSTEM.format(cards=persona_cards(conv.participants), aletheia_id=ALETHEIA_ID)


def format_prompt(conv: Conversation, human: dict) -> str:
    """Le texte de cortex.prompt, au format exact de chaque entrée."""
    if conv.channel == "local":
        return human["text"]                                  # micro local : pas de nom
    name = human["persona"]["display_name"]
    if human["voice"]:
        return f"{name}: {human['text']}"                     # cortex, transcript_prompt_text
    return f"{name} said: {human['text']}"                    # io_discord/cogs/text.py


def draw_intent(conv: Conversation, rng: random.Random) -> tuple[str, str]:
    allowed = []
    for key, weight, text in INTENTS:
        if key == "ecran" and not conv.vision:
            continue
        if key == "souvenir" and not (conv.memory and conv.memory.texts):
            continue
        if key in ("note", "gif") and conv.channel in ("voice", "local"):
            continue
        if key in ("entre_eux", "nouveau") and conv.channel == "local":
            continue
        if key == "entre_eux" and len(conv.participants) < 2:
            continue
        allowed.append((key, weight, text))
    key, _, text = rng.choices(allowed, weights=[w for _, w, _ in allowed])[0]
    return key, text


async def run_conversation(chat, conv: Conversation, cfg: dict, rng: random.Random, emit) -> None:
    def meta(**extra):
        return {"conversation_id": conv.id, "channel": conv.channel, "situation": conv.situation,
                "participants": [p["id"] for p in conv.participants], "prefill": conv.prefill,
                **extra}

    if conv.proactive:
        sample, topic = await topic_turn(chat, conv)
        emit(sample, meta(round=0, intent="topic"))
        conv.transcript.append("(Aletheia s'ennuie et lance d'elle-même un sujet)")
        sample = await aletheia_turn(chat, conv, TOPIC_PREFIX + topic if topic else PLACEHOLDER_TOPIC, rng)
        emit(sample, meta(round=0, intent="proactif"))

    for round_ in range(1, conv.rounds + 1):
        key, intent = draw_intent(conv, rng)
        target = None
        if key == "souvenir":
            # Un souvenir qui existe : get_from_memory a quelque chose à trouver.
            target = rng.choice(conv.memory.texts)
            intent = intent.format(memory=target)
        conv.target = target
        if key == "nouveau":
            name = rng.choice(cfg["unknown_names"])
            if all(p["display_name"] != name for p in conv.participants):
                conv.participants.append({
                    "id": str(rng.randrange(10**17, 10**18)), "display_name": name,
                    "who": "un inconnu qui découvre le serveur, personne ne le connaît encore",
                    "style": "curieux ou méfiant, découvre qu'une IA parle sur le serveur"})
            intent = intent.format(name=name)
        n = rng.randint(2, 3) if key == "entre_eux" else rng.choices([1, 2, 3], [75, 17, 8])[0]
        if conv.channel == "mixed" and key not in ("note", "gif"):
            voices = [rng.random() < 0.5 for _ in range(n)]
        else:
            voices = [conv.channel in ("voice", "local")] * n

        conv.advance(rng.uniform(4, 120))
        humans = await simulate_humans(chat, conv, intent, voices)
        for i, human in enumerate(humans):
            if i:
                # File « le dernier message gagne » : le message précédent est arrivé
                # pendant que la réponse se disait, il va en base sans réponse
                # (src/turn_queue.py).
                conv.rows.append({"role": "user", "content": format_prompt(conv, humans[i - 1]),
                                  "ts": conv.clock})
                conv.advance(rng.uniform(2, 20))
            conv.transcript.append(f"{human['persona']['display_name']}"
                                   f"{' (vocal)' if human['voice'] else ''}: {human['text']}")
        sample = await aletheia_turn(chat, conv, format_prompt(conv, humans[-1]), rng)
        if key == "silence" and sample["outcome"] != "silent":
            # MEMORY.md : un ordre explicite de se taire appelle stay_silent. Sans
            # protocole, Gemma répondait « je me tais » en parlant (7 fois sur 8) :
            # sans ce rejet, l'élève apprendrait à ne jamais se taire.
            sample["rejects"] = sorted(set(sample["rejects"]) | {"silence_ignored"})
        if key == "souvenir" and memory_not_searched(sample, target):
            # Le protocole : un souvenir absent de <recall> se cherche. Sans ce rejet, 6
            # tours « souvenir » sur 19 apprenaient à improviser une réponse.
            sample["rejects"] = sorted(set(sample["rejects"]) | {"memory_not_searched"})
        emit(sample, meta(round=round_, intent=key, burst=len(humans), memory_target=target))
        if {"protocol_leak", "glitch", "tool_as_text"} & set(sample["rejects"]):
            # Le texte fautif est déjà dans l'historique : les tours suivants le verraient,
            # et to_sft les écarterait tous.
            return


# ---------- graines et mémoire factice ----------

def load_seeds(path: str | None, personas: list[dict]) -> tuple[dict, list[str], list[dict]]:
    """(vrais messages par id de persona, vrais souvenirs du RAG, lignes d'historique réelles)."""
    if not path:
        return {}, [], []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    alias = {n: p["id"] for p in personas for n in [p["display_name"], *p.get("aliases", [])]}
    speaker = re.compile(r"(.{1,40}?) said(?: \(voice\))?: (.*)|([^:\[\]{<]{1,30}): (.*)", re.S)
    # Les anciens formats (contenu JSON, horodatage <|...|>) ne passent plus par la production.
    rows = [r for r in data["postgres"]["rows"]
            if r["role"] in ("user", "assistant", "tool") and isinstance(r["content"], str)
            and r["content"][:1] not in "[{<"]
    styles: dict[str, list[str]] = {}
    for row in rows:
        match = speaker.match(row["content"]) if row["role"] == "user" else None
        if not match:
            continue
        name, text = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        if name in alias and 0 < len(text) <= 200:
            styles.setdefault(alias[name], []).append(text)
    memories = [p["payload"]["content"] for p in data["qdrant"]["points"]
                if (p.get("payload") or {}).get("content")]
    return styles, memories, rows


def prompt_facts() -> list[str]:
    """Les faits du prompt système : une ligne par membre de MEMORY.md, et USER.md."""
    with open(os.path.join(CONFIG_DIR, "MEMORY.md"), encoding="utf-8") as f:
        facts = [f"{m.group(2)} (ID {m.group(1)})"
                 for m in re.finditer(r"^- (\d{17,19}) = (.+)$", f.read(), re.M)]
    with open(os.path.join(CONFIG_DIR, "USER.md"), encoding="utf-8") as f:
        user = " ".join(line.strip() for line in f if line.strip())
    return facts + ([user] if user else [])


def saved_memories() -> list[str]:
    """Les save_to_memory des runs précédents : ce qu'Aletheia a déjà noté elle-même."""
    texts = []
    for path in glob.glob(os.path.join(OUT_DIR, "**", "*.jsonl"), recursive=True):
        if os.path.basename(path) == "sft.jsonl":
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                for msg in json.loads(line)["completion"]:
                    for call in msg.get("tool_calls") or []:
                        if (call["function"]["name"] == "save_to_memory" and not tool_problem(call)
                                and not leaks_protocol(call["function"]["arguments"])):
                            texts.append(json.loads(call["function"]["arguments"])["text"])
    return texts


async def invent_memories(chat, cfg: dict, examples: list[str]) -> list[str]:
    """Des souvenirs inventés par Gemma, au style des vrais : sans eux, la mémoire
    compte une cinquantaine d'entrées et les mêmes reviennent sans cesse."""
    personas = cfg["personas"]
    subjects = ([f"{p['display_name']}, {p['who']}" for p in personas for _ in range(2)]
                + ["le serveur Berlin Est, ses blagues internes et son argot"] * 2
                + ["Aletheia elle-même : ce qu'on lui a appris, ses bugs, ses projets de stream"] * 2)
    schema = {"type": "object", "required": ["souvenirs"], "properties": {"souvenirs": {
        "type": "array", "minItems": 12, "maxItems": 12,
        "items": {"type": "string", "minLength": 10, "maxLength": 200}}}}

    async def one(subject: str) -> list[str]:
        prompt = MEMORY_PROMPT.format(cards=persona_cards(personas, examples=False),
                                      examples="\n".join(f"- {e}" for e in examples), subject=subject)
        msg, _, _ = await chat([{"role": "user", "content": prompt}], schema=schema)
        return json.loads(msg.get("content") or "{}").get("souvenirs", [])

    return [m for batch in await asyncio.gather(*(one(s) for s in subjects)) for m in batch]


async def memory_bank(chat, cfg: dict, real: list[str], rebuild: bool) -> list[dict]:
    """La mémoire factice : vrais souvenirs, save_to_memory passés, faits du prompt,
    souvenirs inventés. Construite une fois, puis relue (out/memory_bank.json)."""
    if os.path.exists(BANK_PATH) and not rebuild:
        with open(BANK_PATH, encoding="utf-8") as f:
            return json.load(f)
    bank, seen = [], set()
    sources = [("real", real), ("saved", saved_memories()), ("prompt", prompt_facts()),
               ("gemma", await invent_memories(chat, cfg, real))]
    for source, texts in sources:
        for text in texts:
            if text.strip() and text.strip().lower() not in seen:
                seen.add(text.strip().lower())
                bank.append({"text": text.strip(), "source": source})
    os.makedirs(OUT_DIR, exist_ok=True)
    write_json_atomic(BANK_PATH, bank)
    return bank


def real_prefix(rows: list[dict], rng: random.Random, start: datetime) -> list[dict]:
    """Un morceau de vraie conversation, recalé pour finir avant `start` : en production,
    l'historique d'une nouvelle conversation contient toujours la précédente."""
    if not rows or rng.random() < 0.3:
        return []
    n = rng.randint(4, N_HISTORY)
    i = rng.randrange(max(1, len(rows) - n))
    chunk = rows[i:i + n]
    while chunk and chunk[0]["role"] != "user":
        chunk = chunk[1:]
    if not chunk:
        return []
    shift = (start - timedelta(minutes=rng.randint(10, 3 * 24 * 60))
             - datetime.fromisoformat(chunk[-1]["timestamp"]))
    return [{"role": r["role"], "content": r["content"],
             "ts": datetime.fromisoformat(r["timestamp"]) + shift} for r in chunk]


def new_conversation(cfg: dict, seeds: tuple, rng: random.Random, memory: Memory | None) -> Conversation:
    styles, _, rows = seeds
    personas = cfg["personas"]
    channel = rng.choices(["text", "voice", "mixed", "local"], [55, 30, 10, 5])[0]
    if channel == "local":
        chosen = [personas[0]]                                 # le micro local est celui de Milo
    else:
        k = rng.choices([1, 2, 3], [45, 35, 20])[0]
        chosen = []
        while len(chosen) < k:
            p = rng.choices(personas, [p["weight"] for p in personas])[0]
            if p not in chosen:
                chosen.append(p)
    participants = [{**p, "examples": rng.sample(styles.get(p["id"], []), min(6, len(styles.get(p["id"], []))))}
                    for p in chosen]

    day = datetime.now() - timedelta(days=rng.randint(0, 90))
    hour = rng.choices(list(HOUR_WEIGHTS), list(HOUR_WEIGHTS.values()))[0]
    start = day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)

    mood = None
    if rng.random() < 0.4:
        mood = apply_set(MoodState(), rng.choice(["joyeuse", "taquine", "agacée", "fatiguée", "fière", "triste"]),
                         round(rng.uniform(0.2, 0.9), 2))
    prefix = real_prefix(rows, rng, start)
    conv = Conversation(
        pb=PromptBuilder(), clock=start, participants=participants, channel=channel,
        situation=rng.choice(cfg["situations"]),
        vision=rng.choice(cfg["visions"]) if rng.random() < 0.25 else None,
        mood=mood, rows=prefix, memory=memory.sample(rng) if memory else None, rounds=rng.randint(3, 10),
        proactive=channel != "local" and rng.random() < 0.15, prefill=len(prefix))
    for row in prefix:
        if row["role"] == "user":
            conv.transcript.append(row["content"])
        elif row["role"] == "assistant":
            conv.transcript.append(f"Aletheia: {row['content']}")
    return conv


# ---------- serveur et sorties ----------

def make_chat(client: AsyncOpenAI, model: str):
    async def chat(messages, tools=None, schema=None, prefill=None):
        """Un appel au professeur, sans thinking natif (cf. PROTOCOL). `prefill` impose le
        début de la réponse (continue_final_message de vLLM), et le contenu rendu le
        reprend. Rend (message assistant, finish_reason, usage)."""
        kwargs = {"tools": tools} if tools else {}
        extra = {"top_k": TOP_K, "chat_template_kwargs": {"enable_thinking": False}}
        if prefill:
            messages = messages + [{"role": "assistant", "content": prefill}]
            extra.update(continue_final_message=True, add_generation_prompt=False)
        if schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "sortie", "schema": schema}}
        response = await client.chat.completions.create(
            model=model, messages=messages, temperature=TEMPERATURE, top_p=TOP_P,
            logit_bias=NO_CHANNEL, extra_body=extra, **kwargs)
        choice = response.choices[0]
        raw = choice.message.model_dump()
        content = (prefill or "") + (raw.get("content") or "")
        msg = {"role": "assistant", "content": content or None}
        # vLLM rend la pensée dans `reasoning`. On la range sous `reasoning_content`, le
        # nom que lisent lobe_frontal, le template de Gemma entre deux outils, et LFM3.
        reasoning = raw.get("reasoning") or raw.get("reasoning_content")
        if reasoning:
            msg["reasoning_content"] = reasoning.strip()
        if raw.get("tool_calls"):
            msg["tool_calls"] = [{"id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                  "function": {"name": tc["function"]["name"],
                                               "arguments": tc["function"]["arguments"]}}
                                 for tc in raw["tool_calls"]]
        usage = {}
        if response.usage:
            details = response.usage.completion_tokens_details
            usage = {"prompt_tokens": response.usage.prompt_tokens,
                     "completion_tokens": response.usage.completion_tokens,
                     "reasoning_tokens": (details.reasoning_tokens if details else 0) or 0}
        return msg, choice.finish_reason, usage
    return chat


def latest_export() -> str | None:
    exports = sorted(glob.glob(EXPORT_GLOB), key=os.path.getmtime)
    return exports[-1] if exports else None


def write_json_atomic(path: str, data) -> None:
    """L'agent de nuit lit ce fichier pendant qu'on l'écrit : jamais de fichier à moitié écrit."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="gemma-4-12b")
    parser.add_argument("--parallel", type=int, default=16,
                        help="conversations simultanées (vLLM : --max-num-seqs 32)")
    parser.add_argument("--conversations", type=int, default=0,
                        help="conversations terminées à atteindre ; 0 : jusqu'à l'arrêt")
    parser.add_argument("--export", default=latest_export(), help="export JSON d'hippocampe")
    parser.add_argument("--rebuild-memory", action="store_true", help="reconstruit out/memory_bank.json")
    args = parser.parse_args()

    with open(os.path.join(SYNTH_DIR, "personas.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    seeds = load_seeds(args.export, cfg["personas"])
    chat = make_chat(AsyncOpenAI(base_url=args.url, api_key="sk-vide", timeout=600), args.model)
    bank = await memory_bank(chat, cfg, seeds[1], args.rebuild_memory)
    memory = Memory(make_encoder(), [m["text"] for m in bank])

    os.makedirs(OUT_DIR, exist_ok=True)
    run = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = os.path.join(OUT_DIR, f"{run}.jsonl")
    stats_path = os.path.join(OUT_DIR, "stats.json")
    # L'empreinte du protocole, pas son texte : il ne doit figurer nulle part dans les
    # données, et l'empreinte suffit pour trier les échantillons par version.
    teacher = {"model": args.model, "url": args.url, "temperature": TEMPERATURE, "top_p": TOP_P,
               "top_k": TOP_K, "thinking": False, "reflection": "<reflexion>",
               "protocol": hashlib.sha256(PROTOCOL.encode()).hexdigest()[:12]}
    hashes = config_hashes()
    stats = {"run": out_path, "pid": os.getpid(), "export": args.export, "started_at": datetime.now().isoformat(),
             "updated_at": None, "last_sample_at": None, "memory_bank": dict(Counter(m["source"] for m in bank)),
             "conversations_done": 0, "samples": Counter(), "accepted": 0, "rejected": Counter(),
             "outcomes": Counter(), "intents": Counter(), "tools": Counter(), "recall": Counter(),
             "memory_search": Counter(), "tokens": Counter(), "retries": 0, "errors": 0, "last_error": None}
    print(f"synth : {out_path} | export : {args.export} | mémoire : {stats['memory_bank']} "
          f"| {args.parallel} conversations en parallèle")

    out = open(out_path, "a", encoding="utf-8")

    def flush_stats():
        stats["updated_at"] = datetime.now().isoformat()
        write_json_atomic(stats_path, stats)

    def emit(sample: dict, scenario: dict) -> None:
        now = datetime.now().isoformat()
        record = {"id": uuid.uuid4().hex, "created_at": now, "teacher": teacher,
                  "config_hashes": hashes, "scenario": scenario, **sample}
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        stats["last_sample_at"] = now
        stats["samples"][sample["kind"]] += 1
        stats["accepted"] += not sample["rejects"]
        stats["rejected"].update(sample["rejects"])
        stats["outcomes"][sample["outcome"]] += 1
        stats["intents"][scenario.get("intent")] += 1
        stats["tokens"].update(sample.get("usage") or {})
        stats["retries"] += sample.get("retries", 0)
        if sample["kind"] == "turn":
            stats["recall"]["hit" if sample["context"]["recall"] else "empty"] += 1
        for m in sample["completion"]:
            stats["tools"].update(c["function"]["name"] for c in m.get("tool_calls") or [])
            if m["role"] == "tool" and m["name"] == "get_from_memory":
                stats["memory_search"]["hit" if m["content"] else "empty"] += 1
        flush_stats()

    in_flight = 0

    async def worker() -> None:
        # --conversations compte les conversations terminées, pas commencées : sinon un
        # vLLM tombé 10 minutes brûle toutes les places en échecs (16 workers, un essai
        # toutes les 10 s) et le run finit presque vide.
        nonlocal in_flight
        rng = random.Random()
        while not args.conversations or stats["conversations_done"] + in_flight < args.conversations:
            conv = new_conversation(cfg, seeds, rng, memory)
            in_flight += 1
            try:
                await run_conversation(chat, conv, cfg, rng, emit)
                stats["conversations_done"] += 1
            except Exception as e:  # une conversation ratée ne doit pas arrêter la nuit
                stats["errors"] += 1
                stats["last_error"] = f"{datetime.now().isoformat()} {type(e).__name__}: {e}"[:500]
                flush_stats()
                await asyncio.sleep(10)   # serveur tombé : ne pas boucler à vide
            finally:
                in_flight -= 1

    flush_stats()
    try:
        await asyncio.gather(*(worker() for _ in range(args.parallel)))
    finally:
        out.close()
        flush_stats()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
