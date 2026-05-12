"""
PromptBuilder — Classe responsable de la construction structurée du prompt.

Le lobe_frontal gère TOUT le prompt sauf la mémoire conversationnelle
(historique + RAG) qui est exclusivement gérée par l'hippocampe.

Le system prompt est formaté en XML structuré avec les rubriques:
  <persona>      — identité, comportement, règles
  <core_memory>  — faits persistants (serveur, relations)
  <users>        — profils des utilisateurs connus
  <tools>        — descriptions détaillées des outils disponibles
  <context>      — résumé contextuel des 10 dernières minutes (optionnel)
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger("LobeFrontal.PromptBuilder")


class PromptBuilder:
    """
    Construit le prompt complet pour le LLM à partir des fichiers
    de configuration et du contexte conversationnel.

    Responsabilités:
        - Lire et stocker PERSONA.md, MEMORY.md, USER.md à l'init
        - Construire le system prompt XML structuré
        - Exposer le schema des tools (format OpenAI/Groq)
        - Assembler la liste messages[] finale prête pour l'API
    """

    def __init__(self, config_dir: str = None):
        """
        Initialise le PromptBuilder en lisant les fichiers de config.

        Args:
            config_dir: Chemin vers le dossier config/. Par défaut,
                        résolu relativement à ce fichier source.
        """
        if config_dir is None:
            # src/ -> lobe_frontal/ -> config/
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_dir = os.path.join(base_dir, "config")

        self._persona = self._read_file(config_dir, "PERSONA.md")
        self._core_memory = self._read_file(config_dir, "MEMORY.md")
        self._users = self._read_file(config_dir, "USER.md")

        logger.info(f"✅ PromptBuilder initialisé (config: {config_dir})")

    # ──────────────────────────────────────────────
    #  Fichiers de configuration
    # ──────────────────────────────────────────────

    @staticmethod
    def _read_file(directory: str, filename: str) -> str:
        """Lit un fichier de config. Retourne une string vide si absent."""
        path = os.path.join(directory, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                logger.debug(f"Fichier chargé: {filename} ({len(content)} chars)")
                return content
        except FileNotFoundError:
            logger.warning(f"⚠️ Fichier de config introuvable: {path}")
            return ""

    # ──────────────────────────────────────────────
    #  Tools — Schema OpenAI/Groq
    # ──────────────────────────────────────────────

    @property
    def tools_schema(self) -> list[dict]:
        """
        Retourne la liste des tools au format OpenAI function calling.
        Ce schema est passé séparément à l'API LLM (paramètre `tools`).
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_from_memory",
                    "description": (
                        "Interroge ta mémoire sémantique (RAG) pour récupérer "
                        "des informations pertinentes sur un sujet, une personne, "
                        "ou un événement passé. Utilise cet outil AVANT de répondre "
                        "pour vérifier si tu as des souvenirs liés au sujet. "
                        "C'est ton outil le plus important — n'hésite jamais à l'utiliser."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": (
                                    "Le sujet, mot-clé ou question à rechercher "
                                    "dans ta mémoire sémantique"
                                )
                            }
                        },
                        "required": ["prompt"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "save_to_memory",
                    "description": (
                        "Enregistre une information importante dans ta mémoire "
                        "sémantique (RAG) pour t'en souvenir plus tard. "
                        "TOUJOURS utiliser get_from_memory d'abord pour éviter "
                        "les doublons. Utilise cet outil quand tu apprends quelque "
                        "chose de nouveau sur quelqu'un ou un sujet important, "
                        "ou quand tu vois l'emoji peponotes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": (
                                    "Le texte à sauvegarder dans ta mémoire. "
                                    "Formule-le de manière claire et concise."
                                )
                            }
                        },
                        "required": ["text"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "stay_silent",
                    "description": (
                        "Active le mode silence — tu ne réponds pas du tout. "
                        "Utilise cet outil IMMÉDIATEMENT si tu décides de ne pas "
                        "répondre, si tu ignores quelqu'un, ou si on t'a "
                        "explicitement demandé de te taire. "
                        "Ne génère AUCUN texte quand tu utilises cet outil."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }
        ]

    # ──────────────────────────────────────────────
    #  Tools — Descriptions XML pour le system prompt
    # ──────────────────────────────────────────────

    def _build_tools_xml(self) -> str:
        """Construit la section <tools> du prompt XML."""
        tools_descriptions = {
            "get_from_memory": (
                "Interroge ta mémoire sémantique (RAG) pour récupérer des "
                "informations pertinentes. Utilise-le souvent, idéalement avant "
                "chaque réponse. Si tu ne trouves rien, dis-le simplement."
            ),
            "save_to_memory": (
                "Enregistre une information dans ta mémoire sémantique (RAG). "
                "Utilise get_from_memory avant pour vérifier les doublons. "
                "Utilise-le quand tu apprends quelque chose de nouveau, ou "
                "quand tu vois l'emoji peponotes qui te force à mémoriser "
                "le sujet précédent."
            ),
            "stay_silent": (
                "Active le silence radio. Utilise-le si tu ne veux pas "
                "répondre, si tu ignores quelqu'un, ou si on t'a dit de "
                "te taire. Ne dis rien quand tu l'utilises."
            ),
        }

        lines = ["  <tools>"]
        for name, description in tools_descriptions.items():
            lines.append(f'    <tool name="{name}">')
            lines.append(f"      {description}")
            lines.append("    </tool>")
        lines.append("  </tools>")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    #  System Prompt XML
    # ──────────────────────────────────────────────

    def build_system_prompt(self, context_summary: str = None) -> str:
        """
        Construit le system prompt complet au format XML structuré.

        Args:
            context_summary: Résumé optionnel des 10 dernières minutes
                             fourni par l'hippocampe.

        Returns:
            Le system prompt formaté en XML.
        """
        sections = ["<system>"]

        # Persona
        sections.append("  <persona>")
        sections.append(f"    {self._persona}")
        sections.append("  </persona>")
        sections.append("")

        # Core Memory (mémoire persistante)
        sections.append("  <core_memory>")
        sections.append(f"    {self._core_memory}")
        sections.append("  </core_memory>")
        sections.append("")

        # Utilisateurs
        sections.append("  <users>")
        sections.append(f"    {self._users}")
        sections.append("  </users>")
        sections.append("")

        # Tools
        sections.append(self._build_tools_xml())

        # Contexte conversationnel (optionnel)
        if context_summary:
            sections.append("")
            sections.append('  <context window="10min">')
            sections.append(f"    {context_summary}")
            sections.append("  </context>")

        sections.append("</system>")

        return "\n".join(sections)

    # ──────────────────────────────────────────────
    #  Validation URLs
    # ──────────────────────────────────────────────

    @staticmethod
    def _is_discord_url_expired(url: str) -> bool:
        """Vérifie si une URL Discord CDN est expirée via son token 'ex'."""
        if not isinstance(url, str) or "cdn.discordapp.com/attachments/" not in url:
            return False
            
        import urllib.parse
        import time
        
        try:
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            ex_hex = query.get('ex', [''])[0]
            if ex_hex:
                ex_timestamp = int(ex_hex, 16)
                # Marge de 5 minutes (300s) par sécurité
                if ex_timestamp < time.time() + 300:
                    return True
        except Exception:
            pass
            
        return False

    # ──────────────────────────────────────────────
    #  Construction du prompt complet
    # ──────────────────────────────────────────────

    def build(
        self,
        prompt: str,
        images: list[str] = None,
        history: list[dict] = None,
        context_summary: str = None,
    ) -> list[dict]:
        """
        Construit la liste complète de messages prête pour l'API LLM.

        Args:
            prompt: Le message texte de l'utilisateur.
            images: Liste optionnelle d'URLs d'images.
            history: Historique des N derniers messages (role/content).
            context_summary: Résumé optionnel des 10 dernières minutes.

        Returns:
            Liste de dicts [{role, content}, ...] formatée pour l'API.

        Structure retournée:
            1. System prompt (XML structuré)
            2. Historique conversationnel (messages natifs)
            3. Message utilisateur courant (avec timestamp)
        """
        import copy
        messages = []

        # 1. System prompt XML
        system_prompt = self.build_system_prompt(context_summary)
        messages.append({"role": "system", "content": system_prompt})

        # 2. Historique conversationnel (déjà au format natif role/content)
        if history:
            history_copy = copy.deepcopy(history)
            repaired_history = []
            
            for msg in history_copy:
                # 1. Nettoyage des images Discord expirées
                if isinstance(msg.get("content"), list):
                    cleaned_content = []
                    for item in msg["content"]:
                        if item.get("type") == "image_url":
                            url = item.get("image_url", {}).get("url", "")
                            if self._is_discord_url_expired(url):
                                logger.info("Image Discord expirée retirée de l'historique.")
                                cleaned_content.append({
                                    "type": "text",
                                    "text": "[Image jointe expirée et inaccessible]"
                                })
                                continue
                        cleaned_content.append(item)
                    msg["content"] = cleaned_content
                
                # 2. Réparation des rôles 'tool' orphelins (pour compatibilité stricte Mistral/OpenAI)
                if msg.get("role") == "tool":
                    tool_content = msg.get("content", "")
                    injection = f"\n\n[Information récupérée en mémoire (tool call) : {tool_content}]"
                    
                    if repaired_history:
                        prev_msg = repaired_history[-1]
                        if isinstance(prev_msg.get("content"), list):
                            prev_msg["content"].append({"type": "text", "text": injection})
                        else:
                            prev_msg["content"] = str(prev_msg.get("content") or "") + injection
                    else:
                        msg["role"] = "user"
                        msg["content"] = injection.strip()
                        repaired_history.append(msg)
                else:
                    repaired_history.append(msg)
                    
            messages.extend(repaired_history)

        # 3. Message utilisateur courant
        timestamp_prefix = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "

        if images:
            # Format multimodal (vision)
            user_content = []
            if prompt:
                user_content.append({
                    "type": "text",
                    "text": timestamp_prefix + prompt
                })
            for img_url in images:
                if self._is_discord_url_expired(img_url):
                    logger.info("Image Discord du prompt courant expirée, ignorée.")
                    user_content.append({
                        "type": "text",
                        "text": "[Image jointe expirée et inaccessible]"
                    })
                else:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": img_url}
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            # Format texte simple
            messages.append({
                "role": "user",
                "content": timestamp_prefix + prompt
            })

        # 4. Compacter les messages consécutifs du même rôle (ex: user -> user)
        # Requis pour Mistral qui exige une alternance stricte
        compacted_messages = []
        for msg in messages:
            if compacted_messages and compacted_messages[-1]["role"] == msg["role"] and msg["role"] in ("user", "assistant"):
                prev_msg = compacted_messages[-1]
                
                # On convertit le contenu précédent en liste (format multimodale) s'il ne l'est pas
                if not isinstance(prev_msg["content"], list):
                    prev_msg["content"] = [{"type": "text", "text": prev_msg.get("content") or ""}]
                
                # On convertit le nouveau contenu en liste
                if isinstance(msg.get("content"), list):
                    msg_content_list = msg["content"]
                else:
                    msg_content_list = [{"type": "text", "text": msg.get("content") or ""}]
                
                # On fusionne les deux contenus
                prev_msg["content"].extend(msg_content_list)
            else:
                compacted_messages.append(msg)

        # 5. Mistral exige strictement que le premier message après 'system' soit 'user'
        if len(compacted_messages) > 1 and compacted_messages[1]["role"] == "assistant":
            compacted_messages.pop(1)

        return compacted_messages

    def build_user_content_for_db(
        self,
        prompt: str,
        images: list[str] = None,
    ) -> str | list[dict]:
        """
        Construit le contenu utilisateur à sauvegarder dans l'historique
        hippocampe (sans timestamp — l'hippocampe gère ses propres timestamps).

        Args:
            prompt: Le message texte de l'utilisateur.
            images: Liste optionnelle d'URLs d'images.

        Returns:
            String simple ou liste multimodale pour stockage DB.
        """
        if images:
            content = []
            if prompt:
                content.append({"type": "text", "text": prompt})
            for img_url in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
            return content
        return prompt
