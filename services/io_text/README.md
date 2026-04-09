# I/O Text

Un utilitaire en ligne de commande pour envoyer des messages textuels multi-lignes au système (simule la voix ou les autres entrées capteurs).

### Fonctionnalités :
- Interface interactive multi-lignes en terminal.
- Support des commandes de type éditeur de texte (`:w`, `:q`, etc.).
- Publie les messages structurés sur le topic NATS `io.user.msg.text`.

### Utilisation :
1. Lancez le service : `python main.py`
2. Tapez votre message sur une ou plusieurs lignes.
3. Entrez la commande `:w` ou `:send` sur une ligne vide pour envoyer.
4. `:c` ou `:clear` pour effacer le buffer local sans envoyer.
5. `:q` ou `:quit` pour quitter.
