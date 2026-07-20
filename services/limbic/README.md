# 🎭 Limbic

Service portant les envies et l'humeur d'Aletheia — le socle de sa proactivité (voir issue #9).

## 🎯 Rôle & Responsabilités

- **Source de vérité de l'humeur** : maintient en mémoire l'état de mood courant (émotion, intensité 0-1, nuance libre optionnelle).
- **Application des changements** : reçoit les demandes de changement d'humeur (`limbic.mood.set`) et les applique intégralement.
- **Décroissance naturelle** : sans renforcement, l'intensité décroît périodiquement vers une baseline neutre.
- **Rediffusion** : republie l'état canonique sur chaque changement (set ou décroissance) pour que les autres services restent synchronisés.
- **Jauge d'ennui & proactivité** : le boredom croît à chaque tick et se remet à 0 dès qu'une interaction démarre (`cortex.interaction.started`). Une fois le seuil critique franchi, `limbic` déclenche une interaction proactive (`limbic.proactive.trigger`, sujet placeholder en attendant #15) — mais seulement si quelqu'un est présent en vocal Discord (`io.presence.discord_voice`) et que l'heure courante est dans la fenêtre autorisée. Si un gate est fermé au moment critique, le déclenchement reste en attente : le boredom continue d'accumuler et se déclenche dès que les gates s'ouvrent, sans avoir besoin de re-franchir le seuil.

## ⚙️ Configuration & Lancement

### Dépendances
```bash
pip install -r requirements.txt
```

### Variables d'environnement (`.env`)
- `NATS_URL` : URL du broker NATS (défaut `nats://localhost:4222`).
- `MOOD_DECAY_RATE` : quantité d'intensité perdue à chaque tick de décroissance (défaut `0.05`).
- `TICK_INTERVAL_SECONDS` : intervalle entre deux ticks (mood ET boredom), en secondes (défaut `30`).
- `BOREDOM_INCREMENT_RATE` : quantité de boredom gagnée à chaque tick (défaut `0.01`).
- `BOREDOM_THRESHOLD` : seuil de boredom à partir duquel un déclenchement proactif devient possible (défaut `1.0`).
- `PROACTIVE_GATE_START_HOUR` / `PROACTIVE_GATE_END_HOUR` : fenêtre horaire (heures, 0-23) pendant laquelle un déclenchement proactif est autorisé (défaut `9`-`23`).

### Lancement
```bash
python main.py
```

État en mémoire uniquement : un redémarrage réinitialise l'humeur à neutre (cohérent avec `active_sessions` de Cortex).

## 🔌 Interface NATS
- **S'abonne à** : `limbic.mood.set`, `cortex.interaction.started`, `io.presence.discord_voice`
- **Publie sur** : `limbic.mood.update`, `limbic.proactive.trigger`

## 🧪 Tests
```bash
pytest tests/
```
Couvre le cœur de décision pur : `mood.py` (application d'un set, décroissance, retour à la baseline neutre) et `boredom.py` (accumulation, reset, gates de présence/horaire, comportement de déclenchement en attente).
