# Projet : `io_yeux` — perception visuelle de l’écran pour une VTubeuse IA

Je développe une VTubeuse IA conversationnelle utilisant notamment Qwen3.5-4B via `llama-server`.

Je veux ajouter un service nommé `io_yeux` permettant à la VTubeuse de percevoir ce qui se passe sur mon écran, sans envoyer continuellement des screenshots dans l’historique conversationnel du LLM.

L’objectif n’est pas de créer un agent capable de contrôler l’ordinateur. `io_yeux` est uniquement un système de perception.

## Objectif général

`io_yeux` doit :

1. capturer périodiquement l’écran ;
2. détecter localement si l’image a changé de manière significative ;
3. décider si une nouvelle analyse visuelle par le VLM est nécessaire ;
4. envoyer uniquement les captures pertinentes à Qwen3.5-4B via `llama-server` ;
5. convertir chaque observation en un état textuel/JSON compact ;
6. conserver uniquement l’état visuel courant, et non l’historique des images ;
7. produire éventuellement des événements visuels importants pouvant provoquer une réaction spontanée de la VTubeuse ;
8. permettre au système conversationnel de demander une observation immédiate lorsque le message d’un utilisateur fait référence à ce qui est visible à l’écran.

---

# Principe architectural

Pipeline principal :

```text
DISPLAY
   │
   ▼
io_yeux
   │
   ├── Capture écran
   │
   ├── Resize / crop
   │
   ├── Change detection
   │
   ├── Vision scheduler
   │
   ▼
llama-server
Qwen3.5-4B + mmproj
   │
   ▼
VisualState
   │
   ├── State store
   └── Event bus
          │
          ▼
Character Controller
   │
   ├── enrichissement du contexte conversationnel
   └── réactions spontanées
```

Le principe essentiel est :

```text
IMAGE
  ↓
PERCEPTION
  ↓
ÉTAT TEXTUEL
```

La VTubeuse ne doit pas mémoriser les pixels.

Elle doit mémoriser ce qu’elle a vu.

---

# 1. Capture écran

Le service doit pouvoir capturer :

- l’écran actif ;
- idéalement la fenêtre active ;
- éventuellement un écran particulier ;
- éventuellement une région précise de l’écran.

Les captures complètes ne doivent pas nécessairement être envoyées à leur résolution native.

Pour la perception passive, viser typiquement une image d’environ :

```text
1280×720
```

afin de réduire fortement le nombre de tokens visuels.

Il doit cependant être possible de faire une capture haute résolution ou un crop précis lorsqu’un détail doit être lu.

Exemples :

```text
full_screen
active_monitor
active_window
region_of_interest
```

---

# 2. Détection locale des changements

Ne pas appeler le VLM à chaque capture.

Les screenshots doivent d’abord passer par une détection de changement locale, rapide et peu coûteuse.

Exemple conceptuel :

```python
if screen_change > threshold:
    request_visual_observation()

elif time_since_last_observation > heartbeat_interval:
    request_visual_observation()
```

La première implémentation peut rester simple :

- resize en petite résolution ;
- comparaison entre la frame actuelle et précédente ;
- différence absolue moyenne ;
- histogrammes ;
- SSIM ;
- perceptual hash ;

ou toute autre méthode raisonnable.

Cette détection n’a pas besoin d’être parfaite.

Son rôle est simplement d’éviter de solliciter le VLM lorsque l’écran est quasiment identique.

Prévoir une abstraction afin de pouvoir remplacer la méthode plus tard.

---

# 3. Vision scheduler

Créer un composant chargé de décider quand envoyer une image au VLM.

Il doit gérer plusieurs causes possibles.

## Changement significatif

Exemple :

```text
VS Code
→ Alt+Tab
→ Blender
```

Doit provoquer une nouvelle observation rapidement.

## Heartbeat

Même si l’écran semble statique, une observation périodique doit pouvoir être déclenchée.

Exemple initial configurable :

```text
idle heartbeat : 5–10 secondes
dynamic heartbeat : 2–5 secondes
```

Ces valeurs doivent être configurables.

## Observation forcée

Le reste du système doit pouvoir demander :

```text
observe_now()
```

Exemple :

```text
Viewer:
"Tu vois le message d'erreur ?"
```

Le Character Controller pourra demander à `io_yeux` une capture récente avant de répondre.

## Cache temporel

Si l’état visuel est suffisamment récent, il peut être réutilisé.

Exemple :

```text
visual_state_age < 3 secondes
→ utiliser l’état existant

visual_state_age > 3 secondes
ET message nécessitant la vision
→ observer immédiatement
```

Les valeurs doivent être configurables.

---

# 4. Deux niveaux de perception

Prévoir idéalement deux modes.

## Passive vision

Capture réduite :

```text
~1280×720
```

Objectif :

- application utilisée ;
- activité générale ;
- jeu affiché ;
- événement évident ;
- popup ;
- changement de scène.

Elle doit être peu coûteuse.

## Focused / attentive vision

Déclenchée lorsque davantage de précision est nécessaire.

Elle utilise :

- capture native ;
- fenêtre active ;
- crop ;
- région d’intérêt.

Exemples :

```text
"Lis le message d'erreur."
"Qu'est-ce qui est écrit dans cette fenêtre ?"
"Regarde le terminal."
```

La première version du projet n’a pas nécessairement besoin d’un système automatique complexe de ROI.

Mais l’architecture doit permettre de l’ajouter ensuite.

---

# 5. Utilisation de Qwen3.5-4B

Le VLM est :

```text
Qwen3.5-4B
```

exécuté via :

```text
llama-server
```

GGUF actuellement utilisé :

```text
Qwen3.5-4B-Q6_K.gguf
```

avec le projector multimodal :

```text
mmproj-F16.gguf
```

Le modèle tourne en non-thinking pour minimiser la latence.

Le service doit appeler l’API OpenAI-compatible de `llama-server`.

Le service doit utiliser le serveur llama-server déjà présent (comme dans le service `terminal`)

---

# 6. Prompt du service de perception

`io_yeux` ne doit pas utiliser la personnalité de la VTubeuse.

Le modèle doit agir comme un composant de perception.

Concept :

```text
Tu es le système de perception visuelle d'une VTubeuse IA.

Analyse uniquement l'écran fourni.

Décris factuellement :
- l'application ou le jeu visible ;
- l'activité principale ;
- les éléments importants ;
- les textes pertinents ;
- les changements ou événements remarquables.

Ne converse pas.
Ne t'adresse pas à l'utilisateur.
Ne crée pas de commentaire humoristique.
Retourne uniquement le format JSON demandé.
```

Le prompt final devra être conçu pour obtenir un résultat déterministe et compact.

---

# 7. `VisualState`

Chaque observation doit produire une structure similaire à :

```json
{
  "timestamp": 0,
  "application": "Blender",
  "scene": "Viewport 3D montrant un personnage humanoïde",
  "activity": "L'utilisateur ajuste le rig du bras droit",
  "visible_text": [
    "Pose Mode",
    "Armature"
  ],
  "important_events": [],
  "confidence": 0.86
}
```

La structure exacte doit être définie dans la spec.

Prévoir éventuellement :

```json
{
  "observation_id": "...",
  "timestamp": "...",
  "application": "...",
  "window_title": "...",
  "scene": "...",
  "activity": "...",
  "visible_text": [],
  "entities": [],
  "important_events": [],
  "salience": 0.0,
  "confidence": 0.0
}
```

Éviter les champs inutilement verbeux.

L’objectif est de transformer plusieurs milliers de tokens visuels en quelques dizaines ou centaines de tokens textuels.

---

# 8. `VisualEvent`

Certaines observations doivent pouvoir produire des événements.

Exemples :

```text
application_changed
popup_appeared
error_detected
game_over
crash_detected
scene_changed
unexpected_event
```

Structure possible :

```json
{
  "type": "error_detected",
  "timestamp": "...",
  "description": "Une erreur Python est apparue dans Blender.",
  "salience": 0.91,
  "confidence": 0.84
}
```

Le scheduler ou un composant dédié peut comparer le nouvel état avec l’état précédent afin de détecter ces événements.

---

# 9. Salience

Je veux pouvoir utiliser une valeur de pertinence / importance :

```text
0.0 → changement sans intérêt
1.0 → événement extrêmement important
```

Exemple :

```python
if visual_event.salience >= spontaneous_reaction_threshold:
    event_bus.publish(visual_event)
```

Le Character Controller décidera ensuite s’il faut parler.

`io_yeux` ne doit jamais déclencher directement le TTS.

---

# 10. State store

Le dernier `VisualState` est un état volatile.

Il doit être remplacé à chaque observation.

Ne jamais construire un historique du type :

```text
screenshot 1
screenshot 2
screenshot 3
...
```

dans le contexte du LLM.

Conceptuellement :

```text
latest_visual_state
```

peut être stocké :

- en mémoire pour un premier prototype ;
- puis éventuellement dans Redis.

Exemple de clé :

```text
aletheia:vision:current
```

Un TTL peut être utilisé, par exemple :

```text
30 secondes
```

Le consommateur doit pouvoir savoir si l’état est périmé.

---

# 11. Intégration avec le Character Controller

Le Character Controller ne reçoit normalement pas l’image.

Il reçoit quelque chose comme :

```text
<visual_context>
Application: Blender
Activité: l'utilisateur travaille sur le rig d'un personnage.
Éléments visibles: Pose Mode, Armature.
Dernière observation: il y a 1.8 seconde.
</visual_context>
```

Puis le chat peut produire une réponse normalement.

Exemple :

```text
Viewer:
"Pourquoi il galère autant ?"

Visual context:
Milo travaille sur un rig Blender et ajuste le bras droit du personnage.

VTubeuse:
...
```

La vision enrichit donc le contexte, mais reste indépendante de la personnalité.

---

# 12. Réactions spontanées

Le système doit permettre à terme :

```text
écran
→ événement visuel important
→ event bus
→ Character Controller
→ décision de réaction
→ LLM conversationnel
→ TTS
```

Exemple :

```text
Blender fonctionne normalement
↓
popup de crash
↓
io_yeux détecte un changement majeur
↓
VisualEvent:
{
    "type": "crash_detected",
    "salience": 0.95
}
↓
Character Controller
↓
réaction spontanée éventuelle
```

La décision de parler reste hors de `io_yeux`.

---

# 13. Messages faisant référence à l’écran

Le système conversationnel doit pouvoir utiliser deux stratégies.

### État récent

Si :

```text
visual_state_age <= max_visual_state_age
```

utiliser directement le dernier `VisualState`.

### État trop ancien ou demande explicite

Si le message contient une référence nécessitant une observation récente :

```text
"Tu vois ça ?"
"Regarde l'écran."
"Tu vois le message ?"
"Qu'est-ce qu'il fait ?"
```

le Controller pourra appeler :

```text
io_yeux.observe_now()
```

avant d'envoyer la requête conversationnelle.

Il n’est pas nécessaire que `io_yeux` détecte lui-même les intentions du viewer.

Cette responsabilité peut rester dans le Character Controller.

---

# 14. Gestion multi-écrans

Ne pas envoyer en permanence une énorme image combinée de plusieurs écrans.

Préférer :

```text
active_monitor
```

ou :

```text
active_window
```

La spec doit néanmoins prévoir :

```text
monitor_id
all_monitors
active_monitor
```

À terme, une perception passive pourrait utiliser des thumbnails de chaque écran.

---

# 15. Performance

Le service doit être conçu pour ne pas bloquer le pipeline conversationnel.

Les opérations suivantes doivent être rapides et idéalement indépendantes :

```text
screen capture
change detection
image preprocessing
VLM observation
state update
```

Utiliser de l’async lorsque pertinent.

Ne jamais lancer deux analyses visuelles inutiles simultanément.

Prévoir :

- verrou / semaphore d’observation ;
- possibilité de supprimer une observation devenue obsolète ;
- timeout VLM ;
- retries limités ;
- gestion propre des erreurs `llama-server`.

L’échec de `io_yeux` ne doit jamais empêcher la VTubeuse de continuer à discuter.

---

# 16. Backpressure

Cas important :

```text
capture A
→ analyse VLM en cours

capture B
→ changement

capture C
→ changement
```

Je ne veux pas nécessairement analyser A, B puis C.

Si A est toujours en cours et que B/C arrivent, on peut conserver uniquement la capture la plus récente.

Concept :

```text
latest-frame-wins
```

Une analyse obsolète ne doit pas créer plusieurs secondes de retard.

---

# 17. Configuration

Prévoir une configuration centralisée, par exemple :

```yaml
io_yeux:
  enabled: true

  capture:
    fps: 1
    monitor: active
    passive_width: 1280
    passive_height: 720

  change_detection:
    enabled: true
    threshold: 0.15

  scheduler:
    idle_heartbeat_seconds: 10
    active_heartbeat_seconds: 3
    max_visual_state_age_seconds: 3

  vision:
    base_url: "http://127.0.0.1:8080/v1"
    model: "Qwen3.5-4B-Q6_K.gguf"
    max_tokens: 200
    timeout_seconds: 10

  events:
    spontaneous_threshold: 0.8

  state:
    ttl_seconds: 30
```

Les valeurs sont seulement des defaults de départ et doivent pouvoir être ajustées après benchmark.

---

# 18. Observabilité

Ajouter des métriques/logs permettant de mesurer :

```text
capture latency
change detection latency
VLM latency
VLM prompt processing time
VLM generation time
screenshots skipped
screenshots analyzed
forced observations
heartbeat observations
event count
average VisualState size
errors
```

Je veux notamment pouvoir savoir :

```text
captures/sec
observations VLM/min
latence moyenne d'observation
pourcentage de screenshots ignorés
```

afin de régler correctement les thresholds.

---

# 19. Sécurité / confidentialité

Par défaut, les screenshots ne doivent pas être persistés sur disque.

Workflow souhaité :

```text
capture
→ RAM
→ preprocess
→ llama-server local
→ destruction
```

Prévoir éventuellement un mode debug permettant de sauvegarder ponctuellement les captures, désactivé par défaut.

Ne pas logger :

- contenu complet des screenshots ;
- tokens/secrets visibles ;
- données sensibles extraites par OCR ;

sauf mode debug explicitement activé.

---

# Résumé du comportement attendu

En fonctionnement normal :

```text
1. io_yeux capture régulièrement l'écran.

2. Il compare la nouvelle frame à la précédente.

3. Si presque rien n'a changé :
      aucune requête VLM.

4. Si l'écran a suffisamment changé
   OU si le heartbeat expire
   OU si une observation est explicitement demandée :
      envoyer une capture optimisée à Qwen3.5-4B.

5. Qwen retourne un VisualState JSON compact.

6. Le nouvel état remplace l'ancien.

7. Les changements importants génèrent éventuellement un VisualEvent.

8. Le Character Controller récupère le VisualState lorsque nécessaire.

9. Les screenshots eux-mêmes ne sont jamais ajoutés durablement à l'historique conversationnel.

10. Le Character Controller reste seul responsable de la décision de faire parler la VTubeuse.
```

Principe directeur :

```text
io_yeux observe.
Le Character Controller interprète.
La VTubeuse réagit.
```