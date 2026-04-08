# Hippocampe (La Mémoire)

Service gérant les banques de données persistantes de l'entité.

### Rôle principal :
- **Mémoire Épisodique (RAG) :** Instanciation et requêtage sur une base vectorielle (Qdrant ou Milvus), alimentée par Function Calling.
- **Mémoire Sémantique :** Connecteur PostgreSQL pour stocker l'état global et les données paramétriques (viewers, relations, variables).
