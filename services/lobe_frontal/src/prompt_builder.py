import os
import logging
import time
import urllib.parse
from datetime import datetime

logger = logging.getLogger("LobeFrontal.PromptBuilder")

class PromptBuilder:
    def __init__(self, config_dir: str = None):
        if config_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_dir = os.path.join(base_dir, "config")

        self._persona = self._read_file(config_dir, "PERSONA.md")
        self._core_memory = self._read_file(config_dir, "MEMORY.md")
        self._users = self._read_file(config_dir, "USER.md")
        logger.info(f"✅ PromptBuilder initialisé (config: {config_dir})")

    @staticmethod
    def _read_file(directory: str, filename: str) -> str:
        path = os.path.join(directory, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.warning(f"⚠️ Fichier manquant: {path}")
            return ""

    @property
    def tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_from_memory",
                    "description": "Interroge ta mémoire sémantique (RAG).",
                    "parameters": {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "save_to_memory",
                    "description": "Enregistre une information importante dans ta mémoire sémantique.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "stay_silent",
                    "description": "Active le mode silence si tu décides de ne pas répondre.",
                    "parameters": {"type": "object", "properties": {}},
                    "required": []
                }
            }
        ]

    def _build_tools_xml(self) -> str:
        return (
            "  <tools>\n"
            "    <tool name=\"get_from_memory\">Interroge ta mémoire sémantique (RAG).</tool>\n"
            "    <tool name=\"save_to_memory\">Enregistre une info dans le RAG.</tool>\n"
            "    <tool name=\"stay_silent\">Silence radio total.</tool>\n"
            "  </tools>"
        )

    def build_system_prompt(self, context_summary: str = None) -> str:
        sections = [
            "<system>\n  <persona>", self._persona, "  </persona>\n  <core_memory>",
            self._core_memory, "  </core_memory>\n  <users>", self._users, "  </users>",
            self._build_tools_xml()
        ]
        if context_summary:
            sections.extend(["\n  <context window=\"10min\">", context_summary, "  </context>"])
        sections.append("\n</system>")
        return "\n".join(sections)

    @staticmethod
    def _is_discord_url_expired(url: str, current_time: float) -> bool:
        if not isinstance(url, str) or "cdn.discordapp.com/attachments/" not in url:
            return False
        try:
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            ex_hex = query.get('ex', [''])[0]
            if ex_hex and int(ex_hex, 16) < current_time + 300:
                return True
        except Exception:
            pass
        return False

    def build(self, prompt: str, images: list[str] = None, history: list[dict] = None, context_summary: str = None) -> list[dict]:
        messages = [{"role": "system", "content": self.build_system_prompt(context_summary)}]
        current_time = time.time()

        # Mutation en place ou reconstruction légère (Pas de deepcopy !)
        if history:
            repaired_history = []
            for msg in history:
                content = msg.get("content")
                # Traitement rapide du contenu multimédia
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "image_url":
                            if self._is_discord_url_expired(item.get("image_url", {}).get("url", ""), current_time):
                                item["type"] = "text"
                                item["text"] = "[Image jointe expirée et inaccessible]"
                                item.pop("image_url", None)
                
                # Réparation des rôles 'tool' orphelins
                if msg.get("role") == "tool":
                    injection = f"\n\n[Information mémoire : {msg.get('content', '')}]"
                    if repaired_history:
                        prev = repaired_history[-1]
                        if isinstance(prev.get("content"), list):
                            prev["content"].append({"type": "text", "text": injection})
                        else:
                            prev["content"] = str(prev.get("content") or "") + injection
                    else:
                        repaired_history.append({"role": "user", "content": injection.strip()})
                else:
                    repaired_history.append(msg)
            messages.extend(repaired_history)

        # Ajout du prompt courant
        timestamp_prefix = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        if images:
            user_content = [{"type": "text", "text": timestamp_prefix + prompt}]
            for img_url in images:
                if self._is_discord_url_expired(img_url, current_time):
                    user_content.append({"type": "text", "text": "[Image jointe expirée]"})
                else:
                    user_content.append({"type": "image_url", "image_url": {"url": img_url}})
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": timestamp_prefix + prompt})

        # Compaction ultra-rapide des rôles consécutifs
        compacted = []
        for msg in messages:
            if compacted and compacted[-1]["role"] == msg["role"] and msg["role"] in ("user", "assistant"):
                prev = compacted[-1]
                if not isinstance(prev["content"], list):
                    prev["content"] = [{"type": "text", "text": prev["content"]}]
                
                if isinstance(msg["content"], list):
                    prev["content"].extend(msg["content"])
                else:
                    prev["content"].append({"type": "text", "text": msg["content"]})
            else:
                compacted.append(msg)

        if len(compacted) > 1 and compacted[1]["role"] == "assistant":
            compacted.pop(1)

        return compacted

    def build_user_content_for_db(self, prompt: str, images: list[str] = None) -> str | list[dict]:
        if images:
            content = [{"type": "text", "text": prompt}] if prompt else []
            for img_url in images:
                content.append({"type": "image_url", "image_url": {"url": img_url}})
            return content
        return prompt
