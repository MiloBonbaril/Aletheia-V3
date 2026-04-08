# Cortex (L'Orchestrateur)

Ce micro-service (Rust recommandé) est le "maître du temps".

### Rôle principal :
- S'abonner à tous les topics pertinents sur le broker NATS.
- Gérer la boucle de proactivité (solliciter le LLM s'il n'y a pas d'activité).
- Gérer la préemption (interruption d'urgence si coupure de la parole).
