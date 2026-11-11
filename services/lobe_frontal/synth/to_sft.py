"""Échantillons du générateur → jeu d'entraînement SFT pour l'élève (LFM3-1.7B).

    cd services/lobe_frontal && ../../venv/bin/python -m synth.to_sft [out/*.jsonl] > synth/out/sft.jsonl

Garde les échantillons sans rejet, et rend une ligne {"messages", "tools", "conv_id"} par
échantillon : le contexte puis les tours d'Aletheia, pensée comprise
(`reasoning_content`, que le template de LFM3 lit). `conv_id` sert à séparer
entraînement et évaluation par conversation : les lignes d'une même conversation
partagent leur historique.

Le template de LFM3 refuse les arguments d'outil en chaîne JSON, le format OpenAI
que le générateur garde parce que vLLM l'exige au retour : on les décode ici.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synth.main import GLITCH, TOOL_AS_TEXT, leaks_protocol, strip_stray_quote

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def to_sft(record: dict) -> dict | None:
    if record["rejects"]:
        return None
    completion = []
    for msg in record["completion"]:
        if msg.get("tool_calls"):
            msg = {**msg, "tool_calls": [
                {**c, "function": {**c["function"], "arguments": json.loads(c["function"]["arguments"] or "{}")}}
                for c in msg["tool_calls"]]}
        completion.append(msg)
    # Les runs d'avant split_reflection corrigé gardent le « > » parasite, historique compris.
    messages = [{**m, "content": strip_stray_quote(m["content"]) or None}
                if m["role"] == "assistant" and isinstance(m.get("content"), str) else m
                for m in record["messages"] + completion]
    sample = {"messages": messages}
    if record.get("tools"):
        sample["tools"] = record["tools"]
    sample["conv_id"] = record["scenario"]["conversation_id"]
    # Dernier filet, contexte compris (les tours rejetés restent dans l'historique) :
    # une trace du protocole, un jeton parasite, un outil écrit en texte.
    spoken = "\n".join(m["content"] for m in messages if m["role"] == "assistant" and isinstance(m["content"], str))
    dump = json.dumps(sample, ensure_ascii=False)
    if leaks_protocol(dump) or GLITCH.search(dump) or TOOL_AS_TEXT.search(spoken):
        return None
    return sample


def main() -> None:
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(OUT_DIR, "*.jsonl")))
    kept = total = 0
    for path in paths:
        if os.path.basename(path) == "sft.jsonl":
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                total += 1
                sample = to_sft(json.loads(line))
                if sample:
                    kept += 1
                    print(json.dumps(sample, ensure_ascii=False))
    print(f"{kept}/{total} échantillons gardés", file=sys.stderr)


if __name__ == "__main__":
    main()
