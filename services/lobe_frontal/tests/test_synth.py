"""Le générateur synthétique refait la boucle de run_turn sans serveur : un faux
professeur rejoue des réponses écrites à l'avance."""

import asyncio
import json
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synth.main import (ALETHEIA_ID, N_HISTORY, Conversation, aletheia_turn, clean_human_text,
                        history_for_prompt, recall, text_problems, tool_problem)
from src.prompt_builder import PromptBuilder

T0 = datetime(2026, 10, 3, 21, 15, 0)


def call(name, **args):
    return {"id": f"c-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def fake_teacher(*replies):
    """Rend les réponses dans l'ordre, et garde les messages reçus à chaque appel."""
    seen = []

    async def chat(messages, tools=None, thinking=True, schema=None):
        seen.append(json.loads(json.dumps(messages)))
        content, calls = replies[len(seen) - 1]
        msg = {"role": "assistant", "content": content, "reasoning_content": "je réfléchis"}
        if calls:
            msg["tool_calls"] = calls
        return msg, "tool_calls" if calls else "stop", {"prompt_tokens": 10}
    return chat, seen


def turn(conv, prompt, *replies):
    chat, seen = fake_teacher(*replies)
    return asyncio.run(aletheia_turn(chat, conv, prompt, random.Random(0))), seen


def test_history_keeps_20_rows_and_stamps_user_rows_only():
    rows = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}", "ts": T0}
            for i in range(25)]
    history = history_for_prompt(rows)
    assert len(history) == N_HISTORY
    assert history[-1] == {"role": "user", "content": "[2026-10-03 21:15:00] m24"}
    assert history[-2] == {"role": "assistant", "content": "m23"}


def test_recall_needs_two_shared_words():
    memories = ["Beyou a critiqué la stabilité de mon système", "lolo veut dire poitrine"]
    assert recall(memories, "Beyou critique encore ton système") == memories[0]
    assert recall(memories, "salut Beyou") == ""
    # Une requête d'un seul mot-clé (get_from_memory("le lolo")) doit pouvoir trouver.
    assert recall(memories, "le lolo") == memories[1]


def test_checks():
    assert tool_problem(call("set_mood", emotion="joyeuse", intensity=0.7)) is None
    assert tool_problem(call("set_mood", emotion="joyeuse", intensity=3)) == "invalid_tool"
    assert tool_problem(call("search_web", q="x")) == "invalid_tool"
    assert tool_problem({"function": {"name": "save_to_memory", "arguments": "{trunc"}}) == "invalid_tool"
    assert text_problems("Haha t'as cru 😂") == ["emoji"]
    assert "timestamp" in text_problems("[2026-10-03 21:15:00] coucou")
    assert "speaker_leak" in text_problems("Aletheia: je suis là")
    assert text_problems("Pippen.") == []
    assert text_problems("C'est censé me faire rougir ou quoi ?") == []
    assert text_problems("The tool returned an empty value, meaning it is not in my memory.") == ["english"]


def test_simulated_voice_looks_like_a_whisper_transcript():
    names = {ALETHEIA_ID: "Aletheia"}
    assert clean_human_text("Vegeta (vocal): nan mais attends", "Vegeta", True, names) == "nan mais attends"
    assert clean_human_text(f"<@{ALETHEIA_ID}> t'es là <:Hehee_boi:12>", "Vegeta", True, names) == "Aletheia t'es là"
    # Un message texte garde ses mentions : io_discord les transmet telles quelles.
    assert clean_human_text(f"<@{ALETHEIA_ID}> salut", "Beyou", False, names) == f"<@{ALETHEIA_ID}> salut"


def test_a_tool_call_then_a_reply():
    conv = Conversation(pb=PromptBuilder(), clock=T0)
    sample, seen = turn(conv, "Beyou said: t'es nulle",
                        (None, [call("set_mood", emotion="agacée", intensity=0.6)]),
                        ("Dit celui qui perd aux boîtes.", None))
    assert sample["outcome"] == "reply" and sample["rejects"] == []
    assert [m["role"] for m in sample["completion"]] == ["assistant", "tool", "assistant"]
    assert sample["completion"][1]["content"] == "Mood updated."
    # Le prompt courant porte l'heure simulée, pas l'heure réelle.
    assert sample["messages"][-1]["content"].startswith("[2026-10-03 21:15:01] Beyou said:")
    # Le deuxième appel voit le résultat de l'outil, comme dans run_turn.
    assert seen[1][-1]["role"] == "tool"
    assert conv.mood.emotion == "agacée"
    assert [r["role"] for r in conv.rows] == ["user", "assistant"]


def test_silence_writes_no_assistant_row():
    conv = Conversation(pb=PromptBuilder(), clock=T0)
    sample, _ = turn(conv, "Vegeta said: tais-toi", (None, [call("stay_silent")]))
    assert sample["outcome"] == "silent"
    assert [r["role"] for r in conv.rows] == ["user"]


def test_get_from_memory_goes_to_history_as_a_tool_row():
    conv = Conversation(pb=PromptBuilder(), clock=T0,
                        memories=["Ewyty a crié PAF dans le salon vocal"])
    turn(conv, "Ewyтy said: tu te souviens du PAF ?",
         (None, [call("get_from_memory", prompt="Ewyty crié PAF")]), ("Évidemment.", None))
    assert [r["role"] for r in conv.rows] == ["user", "tool", "assistant"]
    assert conv.rows[1]["content"] == "Ewyty a crié PAF dans le salon vocal"


def test_mood_decays_like_limbic():
    conv = Conversation(pb=PromptBuilder(), clock=T0)
    turn(conv, "Milo said: bravo", (None, [call("set_mood", emotion="fière", intensity=0.1)]), ("Merci.", None))
    conv.advance(60)   # deux ticks de 30 s à 0,05 : retour au neutre
    assert conv.mood.emotion == "neutral" and conv.mood.intensity == 0.0
