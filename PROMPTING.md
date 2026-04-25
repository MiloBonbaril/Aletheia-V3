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
    <!-- Faits persistants, connaissances sur le monde de l'IA, relations stables -->
  </core_memory>

  <users>
    <!-- Profils des utilisateurs connus, préférences, historique relationnel -->
  </users>

  <tools>
    <!-- Descriptions des fonctions disponibles (get_from_memory, save_to_memory, etc.) -->
    <tool name="nom_outil">
      Description de l'utilité et quand l'utiliser.
    </tool>
  </tools>

  <context window="10min">
    <!-- Résumé contextuel récent fourni par l'Hippocampe (optionnel) -->
  </context>
</system>
```

## 🛠️ Outils (Function Calling)

Le système utilise le Function Calling pour interagir avec le monde extérieur et sa propre mémoire.

| Outil | Rôle | Quand l'utiliser ? |
| :--- | :--- | :--- |
| `get_from_memory` | Recherche RAG | **Systématiquement** avant de répondre si le sujet concerne un souvenir ou une personne. |
| `save_to_memory` | Stockage RAG | Lorsqu'une information nouvelle et importante est apprise, ou lors de l'utilisation de l'emoji `peponotes`. |
| `stay_silent` | Contrôle de flux | Pour ignorer un utilisateur ou répondre à une demande de silence. Ne génère aucun texte. |

## 📄 Fichiers de Configuration

Le contenu des balises `<persona>`, `<core_memory>` et `<users>` est dynamiquement injecté depuis des fichiers Markdown situés dans `services/lobe_frontal/config/` :

- **`PERSONA.md`** $\rightarrow$ `<persona>`
- **`MEMORY.md`** $\rightarrow$ `<core_memory>`
- **`USER.md`** $\rightarrow$ `<users>`

L'édition de ces fichiers permet de modifier le comportement de l'IA en temps réel sans modifier le code source.
