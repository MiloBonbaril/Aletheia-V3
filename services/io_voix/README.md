# I/O Voix (TTS)

L'actionneur chargé de donner de la voix au VTubeur.

### Rôle principal :
- S'abonner aux fragments de texte générés par le Lobe Frontal.
- Générer un flux audio extrêmement rapide via `Kokoro ONNX` (Text-to-Speech).
- Router le flux `.wav` vers un câble audio virtuel (VB-Cable) pour être entendu en stream.
