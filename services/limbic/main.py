import asyncio
import json
import os
from datetime import datetime

import nats
from dotenv import load_dotenv

from mood import MoodState, apply_set, decay
from boredom import BoredomState, PLACEHOLDER_TOPIC, tick as boredom_tick, reset as boredom_reset, should_trigger

load_dotenv()

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
MOOD_DECAY_RATE = float(os.getenv("MOOD_DECAY_RATE", "0.05"))
TICK_INTERVAL_SECONDS = float(os.getenv("TICK_INTERVAL_SECONDS", "30"))
BOREDOM_INCREMENT_RATE = float(os.getenv("BOREDOM_INCREMENT_RATE", "0.01"))
BOREDOM_THRESHOLD = float(os.getenv("BOREDOM_THRESHOLD", "1.0"))
PROACTIVE_GATE_START_HOUR = int(os.getenv("PROACTIVE_GATE_START_HOUR", "9"))
PROACTIVE_GATE_END_HOUR = int(os.getenv("PROACTIVE_GATE_END_HOUR", "23"))
# ponytail: constante plutôt qu'un .env — pas un comportement à accorder, juste un garde-fou.
TOPIC_GENERATE_TIMEOUT_SECONDS = 30.0


async def main():
    print("🎭 Démarrage du service Limbic...")
    try:
        nc = await nats.connect(NATS_URL)
        print("✅ Connecté au système nerveux (NATS).")
    except Exception as e:
        print(f"❌ Erreur de connexion à NATS: {e}")
        return

    state = MoodState()
    boredom_state = BoredomState()
    presence_occupied = False

    async def publish_state():
        await nc.publish("limbic.mood.update", json.dumps(state.to_dict()).encode())
        print(f"[Limbic] 🎭 Mood -> {state.emotion} (intensité={state.intensity:.2f})")

    async def mood_set_handler(msg):
        nonlocal state
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            print("[Limbic] ⚠️ Payload mood.set invalide, ignoré.")
            return

        emotion = data.get("emotion")
        intensity = data.get("intensity")
        if emotion is None or intensity is None:
            print("[Limbic] ⚠️ mood.set incomplet (emotion/intensity requis), ignoré.")
            return

        try:
            intensity = float(intensity)
        except (TypeError, ValueError):
            print(f"[Limbic] ⚠️ intensity invalide ({intensity!r}), ignoré.")
            return

        state = apply_set(state, emotion, intensity, data.get("description"))
        await publish_state()

    async def interaction_started_handler(msg):
        nonlocal boredom_state
        boredom_state = boredom_reset(boredom_state)
        print("[Limbic] 💤 Boredom remis à 0 (cortex.interaction.started).")

    async def presence_handler(msg):
        nonlocal presence_occupied
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError:
            print("[Limbic] ⚠️ Payload discord_voice invalide, ignoré.")
            return
        presence_occupied = bool(data.get("occupied", False))

    await nc.subscribe("limbic.mood.set", cb=mood_set_handler)
    await nc.subscribe("cortex.interaction.started", cb=interaction_started_handler)
    await nc.subscribe("io.presence.discord_voice", cb=presence_handler)

    print("👂 Limbic en écoute sur limbic.mood.set, cortex.interaction.started, io.presence.discord_voice")
    print(f"   - Décroissance mood : {MOOD_DECAY_RATE}/tick toutes les {TICK_INTERVAL_SECONDS}s")
    print(f"   - Boredom : +{BOREDOM_INCREMENT_RATE}/tick, seuil={BOREDOM_THRESHOLD}, fenêtre={PROACTIVE_GATE_START_HOUR}h-{PROACTIVE_GATE_END_HOUR}h")

    try:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            new_state = decay(state, MOOD_DECAY_RATE)
            if new_state is not state:
                state = new_state
                await publish_state()

            boredom_state = boredom_tick(boredom_state, BOREDOM_INCREMENT_RATE)
            if should_trigger(
                boredom_state.boredom, BOREDOM_THRESHOLD, presence_occupied,
                datetime.now().hour, PROACTIVE_GATE_START_HOUR, PROACTIVE_GATE_END_HOUR,
            ):
                topic = PLACEHOLDER_TOPIC
                try:
                    reply = await nc.request("lobe.topic.generate", b"{}", timeout=TOPIC_GENERATE_TIMEOUT_SECONDS)
                    topic = json.loads(reply.data.decode()).get("topic") or PLACEHOLDER_TOPIC
                except Exception as e:
                    print(f"[Limbic] ⚠️ lobe.topic.generate indisponible ({e}), fallback sur le placeholder.")

                await nc.publish("limbic.proactive.trigger", json.dumps({
                    "prompt": topic,
                    "source": "proactive",
                }).encode())
                boredom_state = boredom_reset(boredom_state)
                print(f"[Limbic] 🗣️ Déclenchement proactif (boredom -> 0) : {topic}")
    except KeyboardInterrupt:
        print("Arrêt du Limbic...")
    finally:
        await nc.drain()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
