# 🧠 Cortex (L'Orchestrateur)

Le Cortex est le système nerveux central de Nexus-V. Écrit en **Rust**, il assure la coordination et le routage des flux d'événements entre les capteurs (I/O) et le noyau cognitif.

## 🎯 Rôle & Responsabilités

- **Routage d'Événements** : Intercepte les messages des I/O (ex: `io.user.msg.text`) et les dispatch vers le Lobe Frontal (`cortex.prompt`).
- **Gestion de Session** : Suit l'état des conversations en cours via des `correlation_id` et `session_id`.
- **Orchestration du Flux** : Gère la réception des fragments du Lobe Frontal (`lobe.fragment_stream`) pour assurer une distribution fluide vers les sorties.

## ⚙️ Configuration & Lancement

### Dépendances
- [NATS Server](https://nats.io/) (doit être lancé localement sur le port 4222).
- Rust Toolchain (Cargo).

### Variables d'Environnement
- `NATS_URL` : URL du broker NATS (par défaut: `nats://localhost:4222`).

### Lancement
```bash
cargo run --release
```

## 🔌 Interface NATS
- **S'abonne à** : `io.user.msg.text`, `lobe.fragment_stream`
- **Publie sur** : `cortex.prompt`
