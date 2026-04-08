# Lobe Frontal (Gestionnaire LLM)

Ce micro-service (Python) gère l'intelligence principale de l'entité.

### Rôle principal :
- Recevoir les ordres (prompts) de l'Orchestrateur.
- Gérer la conversation avec l'API Groq (Llama 3 8B).
- Maintenir le Context Window et injecter le Persona.
- Gérer le streaming des retours : découper intelligemment les tokens (ex: par phrases) et les publier sur NATS pour exécution.
