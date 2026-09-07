"""Moteur TTS Audio8 : chargement, quantization INT8, et génération par chunk.

Séparé de `main.py` pour que la logique modèle reste lisible sans le bruit NATS.

Le modèle est autorégressif (DualAR), au contraire de Kokoro qui rendait le fragment
entier en une passe. Le coût de synthèse est donc proportionnel à la durée parlée, et
attendre la fin d'un fragment ferait exploser le time-to-first-audio. `stream_fragment`
rend l'audio par tranches : la première est courte (elle fixe le TTFA), les suivantes
grandissent (elles amortissent le surcoût fixe de chaque appel).

Trois optimisations sont nécessaires, pas décoratives. Sans elles le débit mesuré est
RTF 2,2 — plus lent que le temps réel, donc inutilisable :

1. `torch.compile(mode="reduce-overhead")` sur les deux boucles chaudes. Le modèle est
   limité par le lancement des kernels, pas par le calcul : un forward de 4 couches en
   896 de large coûtait 6,4 ms en eager. Les graphes CUDA font passer de 93,6 à
   18,5 ms/frame. Le benchmark officiel du modèle utilise lui aussi CUDA Graph.
2. Le masque d'attention rembourré à largeur fixe. `generate()` fait grandir le masque
   d'un token par frame ; sans rembourrage préalable Dynamo recompile à chaque frame et
   le démarrage ne finit jamais. `_causal_mask` rembourre de toute façon jusqu'à
   `max_seq_len`, donc le résultat est identique.
3. Les caches KV alloués une seule fois. `_setup_generation_caches` les réalloue à
   chaque appel de `generate()` ; les adresses bougent et les graphes CUDA capturés
   deviennent invalides à chaque chunk.
"""

from __future__ import annotations

import os
from typing import Iterator, NamedTuple, Optional

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoProcessor

MODEL_REPO = "Audio8/Audio8-TTS-Preview-0.6b"
# Révision épinglée : le modèle s'exécute avec `trust_remote_code=True`, donc le code
# téléchargé depuis le Hub tourne dans ce processus. On fige ce qui est exécuté.
MODEL_REVISION = "f07040f3d151f1ba0253bfb92cb2f5dd38b44594"
MODEL_DIR = os.getenv(
    "A8_MODEL_DIR", os.path.join(os.path.dirname(__file__), "models", "audio8-tts-0.6b")
)

QUANT = os.getenv("A8_QUANT", "int8").lower()
VOICE_WAV = os.getenv("A8_VOICE_WAV", "").strip()
VOICE_TEXT = os.getenv("A8_VOICE_TEXT", "").strip()
TEMPERATURE = float(os.getenv("A8_TEMPERATURE", "0.7"))
TOP_P = float(os.getenv("A8_TOP_P", "0.9"))
TOP_K = int(os.getenv("A8_TOP_K", "50"))
MAX_FRAMES = int(os.getenv("A8_MAX_FRAMES", "512"))
CHUNK_SCHEDULE = tuple(
    int(part) for part in os.getenv("A8_CHUNK_SCHEDULE", "8,12,24,48,96").split(",") if part.strip()
)


class Engine(NamedTuple):
    """Tout ce que `stream_fragment` doit connaître, monté une fois au démarrage."""

    model: object
    processor: object
    codec: object
    reference_codes: Optional[np.ndarray]
    reference_text: str
    sample_rate: int
    frame_samples: int


def ensure_model() -> str:
    """Télécharge le checkpoint s'il manque. Rend le dossier local."""
    if os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
        return MODEL_DIR
    from huggingface_hub import snapshot_download

    print(f"📥 Téléchargement de {MODEL_REPO} (~1,3 Go)...")
    return snapshot_download(
        MODEL_REPO,
        revision=MODEL_REVISION,
        local_dir=MODEL_DIR,
        allow_patterns=["*.py", "*.json", "*.safetensors", "codec.pth", "tokenizer*"],
    )


def _pin_generation_caches(model) -> None:
    """Alloue les caches KV une seule fois (voir le point 3 de l'en-tête).

    Réutiliser le tampon est sûr : `generate()` réécrit les positions 0..prompt_width à
    chaque appel, et `_causal_mask` masque tout ce qui dépasse le masque d'attention.
    """
    allocate = model._setup_generation_caches
    state: dict = {}

    def setup_once(batch_size, max_length, dtype):
        if state.get("key") == (batch_size, dtype):
            return
        allocate(batch_size, max_length, dtype)
        state["key"] = (batch_size, dtype)

    model._setup_generation_caches = setup_once


def _compile_hot_loops(model) -> None:
    """Capture les deux boucles chaudes en graphes CUDA (points 1 et 2 de l'en-tête)."""
    key_length = model.config.max_seq_len

    # `_fast_step(hidden, position)` reçoit `position` en int Python, de 0 à 9. Dynamo
    # spécialise sur la valeur, donc dix graphes — au-dessus du plafond de huit, où les
    # positions 8 et 9 retombent en eager et perdent le bénéfice des graphes.
    torch._dynamo.config.recompile_limit = max(
        torch._dynamo.config.recompile_limit, model.config.num_codebooks + 4
    )

    # Les sorties d'un graphe CUDA sont des tampons réécrits au replay suivant, et la
    # boucle de `generate()` garde `logits` en vie pendant les 10 pas du Fast AR. Sans
    # `.clone()`, PyTorch lève « accessing tensor output of CUDAGraphs that has been
    # overwritten ». Cloner coûte 311 Kio par frame, soit rien.
    compiled_fast = torch.compile(model._fast_step, mode="reduce-overhead", dynamic=False)
    model._fast_step = lambda hidden, position: compiled_fast(hidden, position).clone()

    eager_slow = model._slow_step
    compiled_slow = torch.compile(eager_slow, mode="reduce-overhead", dynamic=False)

    def slow_step(input_ids, cache_position, position_ids, attention_mask):
        # Le prefill a une largeur variable : il reste en eager. Seul le décodage
        # (1 token) est capturé, et c'est lui qui tourne des centaines de fois.
        if input_ids.shape[-1] != 1:
            return eager_slow(input_ids, cache_position, position_ids, attention_mask)
        mask = F.pad(attention_mask, (0, key_length - attention_mask.shape[1]))
        logits, hidden = compiled_slow(input_ids, cache_position, position_ids, mask)
        return logits.clone(), hidden.clone()

    model._slow_step = slow_step


def _encode_reference(model, processor, wav_path: str, text: str) -> np.ndarray:
    """Encode le wav de référence en codes une fois pour toutes.

    Le clonage zero-shot remplace les voix préréglées de Kokoro : il n'y a plus de
    `ff_siwis`, la voix vient de cet extrait. Encoder à chaque fragment coûterait une
    passe de codec inutile, donc on garde les codes.
    """
    features = processor(
        text=["x"], reference_audio=[wav_path], reference_text=[text], return_tensors="pt"
    )
    with torch.inference_mode():
        codes, lengths = model.encode_audio(
            features["reference_audio_values"].to(model.device),
            features["reference_audio_lengths"].to(model.device),
        )
    return codes[0, :, : int(lengths[0])].cpu().numpy()


def build_engine() -> Engine:
    """Charge le modèle, le quantifie, capture les graphes, et prépare la voix."""
    path = ensure_model()
    processor = AutoProcessor.from_pretrained(path, trust_remote_code=True)
    model = AutoModel.from_pretrained(path, trust_remote_code=True, dtype=torch.bfloat16).eval()

    if torch.cuda.is_available():
        model = model.cuda()
        print(f"🟢 Audio8 sur GPU ({torch.cuda.get_device_name(0)}).")
    else:
        # ponytail: pas de repli CPU calibré. Le modèle est autorégressif ; sur CPU le
        # RTF dépasse largement 1 et le service ne tient aucune cible temps réel. Si le
        # CPU devient nécessaire, la voie prévue par Audio8 est le paquet ONNX INT4,
        # pas ce chemin torch.
        print("🔴 Aucun GPU CUDA — la synthèse sera bien plus lente que le temps réel.")

    if QUANT == "int8":
        from torchao.quantization import Int8WeightOnlyConfig, quantize_

        quantize_(model, Int8WeightOnlyConfig())
        torch.cuda.empty_cache() if torch.cuda.is_available() else None
        print("🔢 Poids en INT8 (torchao, weight-only).")
    else:
        print(f"🔢 Poids en bfloat16 (A8_QUANT={QUANT}).")

    _pin_generation_caches(model)
    if torch.cuda.is_available():
        _compile_hot_loops(model)

    # Le codec reste en float32. Il est plus précis ET plus rapide que bfloat16 ici
    # (127 ms contre 337 ms pour 67 frames : les convolutions bf16 tombent sur de
    # mauvais kernels). La précision compte parce qu'on redécode le préfixe à chaque
    # chunk : en float32 l'écart au raccord tombe à 2e-5, soit -94 dBFS, inaudible.
    codec = model.load_codec(dtype=torch.float32)
    codec.to(device=model.device, dtype=torch.float32)

    reference_codes = None
    if VOICE_WAV:
        if not VOICE_TEXT:
            raise SystemExit("A8_VOICE_WAV est défini mais pas A8_VOICE_TEXT (transcription exacte).")
        reference_codes = _encode_reference(model, processor, VOICE_WAV, VOICE_TEXT)
        print(f"🎙️ Voix de référence : {VOICE_WAV} ({reference_codes.shape[1]} frames).")
    else:
        print("🎙️ Aucune voix de référence — voix par défaut du modèle.")

    return Engine(
        model=model,
        processor=processor,
        codec=codec,
        reference_codes=reference_codes,
        reference_text=VOICE_TEXT,
        sample_rate=model.config.codec_sample_rate,
        frame_samples=model.config.codec_frame_size,
    )


def _decode(engine: Engine, codes: torch.Tensor) -> torch.Tensor:
    """Décode les codes en forme d'onde.

    On n'utilise pas `model.decode_audio` : elle re-caste le codec vers `model.dtype`
    (bfloat16) à chaque appel, ce qui annulerait le float32 choisi plus haut.
    """
    with torch.inference_mode():
        return engine.codec.decode(codes)[0, 0].float()


def _prompt(engine: Engine, text: str):
    kwargs = {"text": [text], "return_tensors": "pt"}
    if engine.reference_codes is not None:
        kwargs["reference_codes"] = [engine.reference_codes]
        kwargs["reference_text"] = [engine.reference_text]
    features = engine.processor(**kwargs)
    features = {name: value.to(engine.model.device) for name, value in features.items()}
    with torch.inference_mode():
        return engine.model._prepare_prompt(**features)


def stream_fragment(engine: Engine, text: str) -> Iterator[np.ndarray]:
    """Rend l'audio d'un fragment par tranches, dès que chaque tranche est prête.

    La génération reprend en réinjectant les codes déjà produits dans `input_ids`, la
    forme d'entrée directe que `generate()` accepte. Le prompt est donc re-prefillé à
    chaque chunk. C'est du travail redondant (le cache KV contient déjà le résultat),
    mais le supprimer demanderait de réécrire `generate()`, et la cadence croissante le
    ramène à quatre ou cinq prefills par fragment.

    ponytail: le décodage est cumulatif — on redécode tout depuis la frame 0 et on
    n'émet que la queue nouvelle. Le codec est entièrement causal (`ArkttsCausalConv1d`,
    `ArkttsCausalConvTranspose1d`), donc le préfixe redécodé est identique au signal
    déjà joué et le raccord est propre sans fondu. Plafond : le décodeur tourne environ
    2 fois plus que nécessaire. Si le décodage devient le poste dominant, la sortie est
    de décoder une fenêtre glissante avec le champ réceptif du décodeur en contexte
    gauche, plutôt que tout l'historique.
    """
    model = engine.model
    semantic_begin = model.config.semantic_begin_id
    prompt, mask = _prompt(engine, text)

    codes = None
    emitted = 0
    step = 0
    while True:
        budget = CHUNK_SCHEDULE[min(step, len(CHUNK_SCHEDULE) - 1)]
        step += 1
        with torch.inference_mode():
            output = model.generate(
                input_ids=prompt,
                attention_mask=mask,
                max_new_tokens=budget,
                do_sample=True,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                top_k=TOP_K,
                return_dict_in_generate=True,
            )
        fresh = output.codes
        if fresh.shape[-1] == 0:
            return

        codes = fresh if codes is None else torch.cat((codes, fresh), dim=-1)
        waveform = _decode(engine, codes)
        if waveform.numel() > emitted:
            yield waveform[emitted:].cpu().numpy().astype(np.float32)
            emitted = waveform.numel()

        if bool(output.finished.all()) or codes.shape[-1] >= MAX_FRAMES:
            return

        # Colonne de reprise : ligne 0 = token sémantique, lignes 1..10 = codebooks.
        # C'est la disposition que `_prepare_prompt` construit pour les codes de
        # référence, et celle que la boucle interne de `generate()` réinjecte.
        column = torch.cat((fresh[:, :1] + semantic_begin, fresh), dim=1)
        prompt = torch.cat((prompt, column), dim=2)
        mask = torch.cat(
            (mask, torch.ones(1, fresh.shape[-1], dtype=mask.dtype, device=mask.device)), dim=1
        )


def warmup(engine: Engine) -> None:
    """Force la compilation et la capture des graphes avant le premier vrai fragment."""
    for _ in stream_fragment(engine, "Bonjour."):
        pass


if __name__ == "__main__":
    # Auto-vérification : la reprise par chunk doit produire un signal continu, et la
    # cadence doit tenir le temps réel (chaque tranche arrive avant que la précédente
    # ait fini de jouer).
    import time

    engine = build_engine()
    print("⏳ Compilation...")
    started = time.perf_counter()
    warmup(engine)
    print(f"   {time.perf_counter() - started:.1f}s")

    text = "Salut tout le monde, je teste ma nouvelle voix de synthese, et j'espere qu'elle vous plaira."
    started = time.perf_counter()
    pieces, marks = [], []
    for chunk in stream_fragment(engine, text):
        marks.append((time.perf_counter() - started, len(chunk) / engine.sample_rate))
        pieces.append(chunk)
    total = time.perf_counter() - started
    audio = np.concatenate(pieces)
    duration = len(audio) / engine.sample_rate

    print(f"\nTTFA {marks[0][0] * 1000:.0f} ms | audio {duration:.2f}s | total {total:.2f}s "
          f"| RTF {total / duration:.3f} | {len(marks)} chunks")
    played = marks[0][0]
    for index, (arrival, length) in enumerate(marks):
        margin = played - arrival
        print(f"  chunk {index}: arrive à {arrival * 1000:6.0f} ms, "
              f"+{length * 1000:6.0f} ms d'audio, marge {margin * 1000:+6.0f} ms")
        assert index == 0 or margin > 0, f"sous-alimentation du buffer au chunk {index}"
        played += length

    assert duration > 1.0, "audio trop court"
    assert np.abs(audio).max() < 1.01, "signal saturé"
    # Un raccord raté s'entend comme un clic : discontinuité brutale entre deux samples.
    jumps = np.abs(np.diff(audio))
    assert jumps.max() < 0.35, f"discontinuité suspecte dans le signal : {jumps.max():.3f}"
    print(f"\nOK — saut max entre deux samples {jumps.max():.4f}, RTF {total / duration:.3f}")
