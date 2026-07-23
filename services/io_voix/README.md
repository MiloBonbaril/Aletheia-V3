# I/O Voix (TTS)

L'actionneur chargé de donner de la voix au VTubeur.

### Rôle principal :
- S'abonner aux fragments de texte générés par le Lobe Frontal (`lobe.fragment_stream`).
- Générer un flux audio réactif et continu via `Kokoro ONNX` (Text-to-Speech).
- Router le flux audio vers le périphérique par défaut (ou un câble audio virtuel comme VB-Cable) en enchaînant les fragments sans coupure.
- Publier l'audio synthétisé de chaque fragment sur `io.voice.speak.audio` (en plus des événements de timing `io.voice.speak.start`/`.end`), pour que d'autres services (ex: un bridge Discord) puissent le jouer sans dépendre du haut-parleur local.

### Particularités :
- Les fichiers volumineux du modèle Kokoro (`~350Mo`) sont téléchargés automatiquement dans le dossier `models/` au premier lancement de l'application si vous ne les avez pas.
- La voix utilisée par défaut est la voix française féminine `ff_siwis`, configurable via la variable d'environnement `KOKORO_VOICE`.
- Le dossier cible du téléchargement est customisable via la variable d'environnement `KOKORO_MODELS_DIR`.
- `MUTE_LOCAL_PLAYBACK=1` coupe la lecture locale (aucun `sd.OutputStream` ouvert) ; la publication NATS (timing + audio) continue normalement — utile quand l'audio ne doit sortir que par un autre canal (ex: Discord).

### Lancement :
```bash
python main.py
MUTE_LOCAL_PLAYBACK=1 python main.py   # pas de lecture locale, uniquement NATS
```

## 🔌 Interface NATS
- **S'abonne à** : `lobe.fragment_stream`
- **Publie sur** : `io.voice.speak.start`, `io.voice.speak.audio`, `io.voice.speak.end`

## 🧪 Tests
```bash
pytest tests/
```
