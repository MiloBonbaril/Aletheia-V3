# 🎭 Limbic

Service portant les envies et l'humeur d'Aletheia — le socle de sa proactivité (voir issue #9).

## 🎯 Rôle & Responsabilités

- **Source de vérité de l'humeur** : maintient en mémoire l'état de mood courant (émotion, intensité 0-1, nuance libre optionnelle).
- **Application des changements** : reçoit les demandes de changement d'humeur (`limbic.mood.set`) et les applique intégralement.
- **Décroissance naturelle** : sans renforcement, l'intensité décroît périodiquement vers une baseline neutre.
- **Rediffusion** : republie l'état canonique sur chaque changement (set ou décroissance) pour que les autres services restent synchronisés.

## ⚙️ Configuration & Lancement

### Dépendances
```bash
pip install -r requirements.txt
```

### Variables d'environnement (`.env`)
- `NATS_URL` : URL du broker NATS (défaut `nats://localhost:4222`).
- `MOOD_DECAY_RATE` : quantité d'intensité perdue à chaque tick de décroissance (défaut `0.05`).
- `MOOD_DECAY_INTERVAL_SECONDS` : intervalle entre deux ticks de décroissance, en secondes (défaut `30`).

### Lancement
```bash
python main.py
```

État en mémoire uniquement : un redémarrage réinitialise l'humeur à neutre (cohérent avec `active_sessions` de Cortex).

## 🔌 Interface NATS
- **S'abonne à** : `limbic.mood.set`
- **Publie sur** : `limbic.mood.update`

## 🧪 Tests
```bash
pytest tests/
```
Couvre le cœur de décision pur (`mood.py`) : application d'un set, décroissance, retour à la baseline neutre.
