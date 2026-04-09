# I/O Voix (TTS)

L'actionneur chargé de donner de la voix au VTubeur.

### Rôle principal :
- S'abonner aux fragments de texte générés par le Lobe Frontal (`lobe.fragment_stream`).
- Générer un flux audio réactif et continu via `Kokoro ONNX` (Text-to-Speech).
- Router le flux audio vers le périphérique par défaut (ou un câble audio virtuel comme VB-Cable) en enchaînant les fragments sans coupure.

### Particularités :
- Les fichiers volumineux du modèle Kokoro (`~350Mo`) sont téléchargés automatiquement dans le dossier `models/` au premier lancement de l'application si vous ne les avez pas.
- La voix utilisée par défaut est la voix française féminine `ff_siwis`, configurable via la variable d'environnement `KOKORO_VOICE`.
- Le dossier cible du téléchargement est customisable via la variable d'environnement `KOKORO_MODELS_DIR`.

### Lancement :
```bash
python main.py
```
