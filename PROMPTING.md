# 🧠 Ingénierie du Prompt (Nexus-V)

Le Lobe Frontal utilise une approche de **Prompting Structuré en XML**. Cette méthode permet au LLM de distinguer clairement les différentes sources d'information et d'appliquer des règles de comportement strictes sans confusion.

## 🏗️ Structure du System Prompt

Le prompt système est encapsulé dans une balise `<system>` et divisé en sections sémantiques :

```xml
<system>
  <persona>
    <!-- Identité, traits de personnalité, ton, règles de langage et interdits -->
  </persona>

  <core_memory>
    <!-- Faits persistants, connaissances sur le monde de l'IA, relations viewers, état global -->
  </core_memory>

  <users>
    <!-- Profils des utilisateurs connus, préférences, historique relationnel -->
  </users>

  <tools>
    <!-- Descriptions des fonctions disponibles (save_to_memory, get_from_memory, etc.) -->
    <tool name="nom_outil">
      Description de l'utilité et quand l'utiliser.
    </tool>
  </tools>

  <mood>
    <!-- Humeur courante d'Aletheia, injectée dynamiquement depuis le dernier état
         rediffusé par le service limbic (limbic.mood.update). Absente tant qu'aucun
         mood n'a encore été fixé (ex: limbic pas encore démarré). -->
  </mood>

  <recall>
    <!-- Souvenirs pertinents rappelés automatiquement par le RAG passif.
         Cette section est injectée dynamiquement avant chaque inférence
         en fonction du message de l'utilisateur. -->
  </recall>

  <context window="10min">
    <!-- Résumé contextuel récent fourni par l'Hippocampe (optionnel) -->
  </context>
</system>
```

## 🛠️ Outils (Function Calling)

Le système utilise le Function Calling pour interagir avec le monde extérieur et sa propre mémoire.

| Outil | Rôle | Quand l'utiliser ? |
| :--- | :--- | :--- |
| `get_from_memory` | Recherche RAG active | Pour des recherches ciblées et complexes que le rappel automatique (`<recall>`) n'aurait pas couvert. |
| `save_to_memory` | Stockage RAG | Lorsqu'une information nouvelle et importante est apprise, ou lors de l'utilisation de l'emoji `peponotes`. |
| `stay_silent` | Contrôle de flux | Pour ignorer un utilisateur ou répondre à une demande de silence. Ne génère aucun texte. |
| `set_mood` | Humeur | Lorsqu'une émotion forte anime Aletheia (joie, tristesse, colère, taquinerie...). Prend `emotion`, `intensity` (0-1) et une `description` libre optionnelle. |

> **Note :** Les souvenirs pertinents sont désormais **automatiquement rappelés** via le RAG passif et injectés dans la section `<recall>` du prompt. Le tool `get_from_memory` reste disponible comme complément pour des requêtes complexes.

> **Note :** `set_mood` ne fait que publier l'intention sur `limbic.mood.set` (fire-and-forget) — le service `limbic` reste seul responsable d'appliquer et de rediffuser l'état canonique via `limbic.mood.update`. Le Lobe Frontal se contente de mettre en cache la dernière valeur reçue et de l'injecter dans `<mood>` au tour suivant ; il ne modifie jamais l'humeur localement.

## 📄 Fichiers de Configuration

Le contenu des balises `<persona>`, `<core_memory>` et `<users>` est dynamiquement injecté depuis des fichiers Markdown situés dans `services/lobe_frontal/config/` :

- **`PERSONA.md`** $\rightarrow$ `<persona>`
- **`MEMORY.md`** $\rightarrow$ `<core_memory>`
- **`USER.md`** $\rightarrow$ `<users>`

L'édition de ces fichiers permet de modifier le comportement de l'IA en temps réel sans modifier le code source.
