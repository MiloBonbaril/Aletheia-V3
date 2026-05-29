# ⚡ Service de Benchmark Continu Aletheia

Ce service permet de mesurer les performances temporelles et la latence d'Aletheia en continu sur le bus NATS, afin de s'assurer du respect des exigences temps réel (latence ultra-faible).

Il utilise un graphe d'événements décrit sous format JSON (comme `graphs/E2E.json`) pour s'abonner dynamiquement aux topics NATS pertinents et retracer le cycle de vie complet de chaque message.

---

## 📈 Métriques Mesurées (End-to-End)

Le graphe par défaut `graphs/E2E.json` retrace la totalité du flux et calcule les latences critiques suivantes :

1. **Liaison Cortex** : Temps nécessaire au Cortex pour recevoir le message utilisateur (`io.user.msg.text` ou `io.user.speak`) et le propager au Lobe Frontal (`cortex.prompt`).
2. **Inférence LLM (TTFT - Time-to-First-Token)** : Le temps d'attente avant le premier fragment de réponse du Lobe Frontal (`lobe.fragment_stream` séquence 0).
3. **Temps de Synthèse Audio (TTS Ingestion)** : Délai entre la réception du premier token textuel et le début effectif de la diction par Kokoro ONNX (`io.voice.speak.start`).
4. **Temps de Génération LLM** : Durée totale de génération textuelle du Lobe Frontal (du premier au dernier fragment `is_last = true`).
5. **Durée de Lecture Vocale** : Temps total où Aletheia parle physiquement (`io.voice.speak.start` à `io.voice.speak.end`).
6. **Latence End-to-End Totale** : Temps complet écoulé entre la requête de l'utilisateur et la fin de l'élocution d'Aletheia.

---

## 🛠️ Installation des Dépendances

Le service nécessite les paquets listés dans `requirements.txt` (notamment `nats-py` et `rich` pour l'interface console premium) :

```bash
pip install -r requirements.txt
```

---

## 🚀 Lancement du Benchmark

Pour démarrer l'écoute en continu avec le graphe E2E par défaut :

```bash
python main.py
```

Pour utiliser un autre graphe JSON personnalisé :

```bash
python main.py /chemin/vers/votre_graphe.json
```

---

## 📊 Exemple de Rendu Visuel

Le service affiche en temps réel les étapes franchies par le flux, puis dessine un rapport complet avec un tableau de synthèse industriel et une **timeline horizontale colorée** :

```
⌛ TIMELINE VISUELLE DE LA LATENCE ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
████░░░░░░░░░░░░░░░░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓

 ■ Liaison Cortex: 15.2 ms   ■ Inférence LLM (TTFT): 824.5 ms   ■ Synthèse TTS: 345.1 ms   ■ Lecture Audio: 2.12 s

⏱️  Latence End-to-End Totale: 3.30 s
```

