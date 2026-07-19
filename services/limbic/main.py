import asyncio
import json
import os

import nats
from dotenv import load_dotenv

from mood import MoodState, apply_set, decay

load_dotenv()

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
MOOD_DECAY_RATE = float(os.getenv("MOOD_DECAY_RATE", "0.05"))
MOOD_DECAY_INTERVAL_SECONDS = float(os.getenv("MOOD_DECAY_INTERVAL_SECONDS", "30"))


async def main():
    print("🎭 Démarrage du service Limbic...")
    try:
        nc = await nats.connect(NATS_URL)
        print("✅ Connecté au système nerveux (NATS).")
    except Exception as e:
        print(f"❌ Erreur de connexion à NATS: {e}")
        return

    state = MoodState()

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

    await nc.subscribe("limbic.mood.set", cb=mood_set_handler)

    print("👂 Limbic en écoute sur limbic.mood.set")
    print(f"   - Décroissance : {MOOD_DECAY_RATE}/tick toutes les {MOOD_DECAY_INTERVAL_SECONDS}s")

    try:
        while True:
            await asyncio.sleep(MOOD_DECAY_INTERVAL_SECONDS)
            new_state = decay(state, MOOD_DECAY_RATE)
            if new_state is not state:
                state = new_state
                await publish_state()
    except KeyboardInterrupt:
        print("Arrêt du Limbic...")
    finally:
        await nc.drain()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
