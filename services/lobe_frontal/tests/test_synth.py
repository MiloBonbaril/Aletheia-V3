"""Le générateur synthétique refait la boucle de run_turn sans serveur : un faux
professeur rejoue des réponses écrites à l'avance, et un faux encodeur remplace
all-MiniLM-L6-v2."""

import asyncio
import json
import os
import random
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synth.main import (ALETHEIA_ID, N_HISTORY, Conversation, Memory, aletheia_turn,
                        clean_human_text, history_for_prompt, memory_not_searched, text_problems,
                        thought_problems, tool_problem, turn_problems)
from src.prompt_builder import PromptBuilder

T0 = datetime(2026, 10, 3, 21, 15, 0)
THOUGHT = "Je sais que c'est un test. On me demande de répondre. Je vais répondre court."
VOCAB = ["beyou", "système", "lolo", "paf", "ewyty"]


def fake_encode(texts):
    """Un sac de mots sur VOCAB, normalisé : la similarité cosinus reste lisible."""
    v = np.array([[float(w in t.lower()) for w in VOCAB] for t in texts]).reshape(len(texts), len(VOCAB))
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(norms == 0, 1, norms)


def call(name, **args):
    return {"id": f"c-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def fake_teacher(*replies, reflect=True):
    """Rend les réponses dans l'ordre, et garde les messages reçus à chaque appel."""
    seen, prefills = [], []

    async def chat(messages, tools=None, schema=None, prefill=None):
        seen.append(json.loads(json.dumps(messages)))
        prefills.append(prefill)
        content, calls = replies[min(len(seen), len(replies)) - 1]   # la dernière se répète
        # Le professeur écrit sa réflexion en tête, dans le bloc <reflexion> (cf. PROTOCOL).
        body = content if reflect else (content or "")
        msg = {"role": "assistant", "content": f"<reflexion>{THOUGHT}</reflexion>{body or ''}" if reflect else body}
        if calls:
            msg["tool_calls"] = calls
        return msg, "tool_calls" if calls else "stop", {"prompt_tokens": 10}
    chat.prefills = prefills
    return chat, seen


def conversation(*memories):
    return Conversation(pb=PromptBuilder(), clock=T0, memory=Memory(fake_encode, memories))


def turn(conv, prompt, *replies, reflect=True):
    chat, seen = fake_teacher(*replies, reflect=reflect)
    sample = asyncio.run(aletheia_turn(chat, conv, prompt, random.Random(0)))
    sample["_prefills"] = chat.prefills
    return sample, seen


def test_history_keeps_20_rows_and_stamps_user_rows_only():
    rows = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}", "ts": T0}
            for i in range(25)]
    history = history_for_prompt(rows)
    assert len(history) == N_HISTORY
    assert history[-1] == {"role": "user", "content": "[2026-10-03 21:15:00] m24"}
    assert history[-2] == {"role": "assistant", "content": "m23"}


def test_memory_search_keeps_the_threshold_and_finds_a_new_save():
    memory = Memory(fake_encode, ["Beyou critique mon système", "le lolo est une boîte"])
    assert memory.search("Beyou et mon système") == "Beyou critique mon système"
    assert memory.search("rien à voir") == ""
    memory.add("Ewyty a crié PAF")
    assert memory.search("ewyty paf") == "Ewyty a crié PAF"
    # Une conversation tire entre 26 souvenirs et toute la banque.
    bank = Memory(fake_encode, [f"souvenir {i}" for i in range(40)])
    sizes = {len(bank.sample(random.Random(seed)).texts) for seed in range(30)}
    assert min(sizes) >= 26 and max(sizes) <= 40 and len(sizes) > 1


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
    assert text_problems("Je sais que tu me cherches. On me demande de répondre.") == ["protocol_leak"]
    assert text_problems("je ne suis pas juste une jolie啦 interface") == ["glitch"]
    assert text_problems('set_mood(emotion="joyeuse", intensity=0.8)\n\nMerci !') == ["tool_as_text"]
    assert text_problems("Ewyтy, t'es sérieux ?") == []
    assert "glitch" in thought_problems(THOUGHT + " ta针对")


def test_a_broken_or_spammed_mention_is_rejected():
    assert text_problems("Demande à <@293414992663805952> !") == []
    assert "bad_mention" in text_problems("]<@293414992663805952] t'es là ?")
    assert "bad_mention" in text_problems("<@1> <@2> <@3> réveillez-vous")


def test_the_thought_follows_the_protocol():
    assert thought_problems(THOUGHT) == []
    assert thought_problems("") == ["no_thought"]
    assert "thought_english" in thought_problems("The user is asking what I know. I need to check memory.")
    assert "long_thought" in thought_problems(" ".join(["Je sais que c'est long."] * 7) + " Je vais répondre.")
    assert thought_problems("Je sais que Beyou me taquine. On me demande de réagir.") == ["thought_format"]


def test_a_search_loop_and_a_repeated_call_are_rejected():
    assistant = lambda *calls: {"role": "assistant", "reasoning_content": THOUGHT, "tool_calls": list(calls)}
    loop = [assistant(call("get_from_memory", prompt=f"q{i}")) for i in range(3)]
    assert turn_problems(loop) == ["memory_loop"]
    twice = [assistant(call("save_to_memory", text="x")), assistant(call("save_to_memory", text="x"))]
    assert turn_problems(twice) == ["duplicate_tool"]


def test_the_protocol_reaches_the_teacher_but_never_the_sample():
    sample, seen = turn(conversation(), "Beyou said: salut", ("Salut toi.", None))
    assert "<protocole>" in seen[0][-1]["content"]          # à la fin du dernier message
    assert "<protocole>" not in json.dumps(sample, ensure_ascii=False)
    # La réflexion sort du texte prononcé et passe dans reasoning_content.
    assert sample["completion"][-1]["content"] == "Salut toi."
    assert sample["completion"][-1]["reasoning_content"] == THOUGHT
    assert "reflexion>" not in json.dumps({k: v for k, v in sample.items() if k != "_prefills"}, ensure_ascii=False)


def test_a_turn_without_reflection_is_rejected():
    sample, seen = turn(conversation(), "Beyou said: salut", ("Salut toi.", None), reflect=False)
    assert "no_thought" in sample["rejects"]
    # L'appel est refait deux fois avant d'abandonner (REFLECTION_TRIES).
    assert len(seen) == 3 and sample["retries"] == 2


def test_simulated_voice_looks_like_a_whisper_transcript():
    names = {ALETHEIA_ID: "Aletheia"}
    assert clean_human_text("Vegeta (vocal): nan mais attends", "Vegeta", True, names) == "nan mais attends"
    assert clean_human_text(f"<@{ALETHEIA_ID}> t'es là <:Hehee_boi:12>", "Vegeta", True, names) == "Aletheia t'es là"
    # Un message texte garde ses mentions : io_discord les transmet telles quelles.
    assert clean_human_text(f"<@{ALETHEIA_ID}> salut", "Beyou", False, names) == f"<@{ALETHEIA_ID}> salut"


def test_a_tool_call_then_a_reply():
    conv = conversation()
    sample, seen = turn(conv, "Beyou said: t'es nulle",
                        (None, [call("set_mood", emotion="agacée", intensity=0.6)]),
                        ("Dit celui qui perd aux boîtes.", None))
    assert sample["outcome"] == "reply" and sample["rejects"] == []
    assert [m["role"] for m in sample["completion"]] == ["assistant", "tool", "assistant"]
    assert sample["completion"][1]["content"] == "Mood updated."
    # Le prompt courant porte l'heure simulée, pas l'heure réelle.
    assert sample["messages"][-1]["content"].startswith("[2026-10-03 21:15:01] Beyou said:")
    # Le deuxième appel voit le résultat de l'outil, comme dans run_turn, et le
    # professeur relit sa réflexion telle qu'il l'a écrite.
    assert seen[1][-1]["role"] == "tool"
    assert seen[1][-2]["content"].startswith("<reflexion>")
    # Après un outil, la réponse est ouverte par <reflexion> ; pas au premier appel.
    assert sample["_prefills"] == [None, "<reflexion>\n"]
    assert conv.mood.emotion == "agacée"
    assert [r["role"] for r in conv.rows] == ["user", "assistant"]


def test_silence_writes_no_assistant_row():
    conv = conversation()
    sample, _ = turn(conv, "Vegeta said: tais-toi", (None, [call("stay_silent")]))
    assert sample["outcome"] == "silent"
    assert [r["role"] for r in conv.rows] == ["user"]


def test_get_from_memory_returns_the_memory_and_goes_to_history():
    conv = conversation("Ewyty a crié PAF dans le salon vocal")
    sample, _ = turn(conv, "Ewyтy said: tu te rappelles ?",
                     (None, [call("get_from_memory", prompt="Ewyty PAF")]), ("Évidemment.", None))
    assert sample["completion"][1]["content"] == "Ewyty a crié PAF dans le salon vocal"
    assert [r["role"] for r in conv.rows] == ["user", "tool", "assistant"]


def test_a_souvenir_turn_puts_the_target_first():
    conv = conversation("Beyou critique mon système", "le lolo est une boîte")
    conv.target = "le lolo est une boîte"
    sample, _ = turn(conv, "Beyou said: tu te rappelles ?",
                     (None, [call("get_from_memory", prompt="Beyou système")]), ("Oui.", None))
    assert sample["completion"][1]["content"] == "le lolo est une boîte\nBeyou critique mon système"


def test_a_souvenir_answered_without_searching_is_caught():
    target = "le lolo est une boîte"
    improvised, _ = turn(conversation(target), "Beyou said: tu te rappelles ?", ("Bien sûr.", None))
    assert memory_not_searched(improvised, target)
    searched, _ = turn(conversation(target), "Beyou said: tu te rappelles ?",
                       (None, [call("get_from_memory", prompt="lolo")]), ("Oui.", None))
    assert not memory_not_searched(searched, target)
    # Déjà dans <recall> : pas besoin de chercher.
    in_recall, _ = turn(conversation(target), "Beyou said: le lolo, une boîte ?", ("Oui.", None))
    assert not memory_not_searched(in_recall, target)


def test_a_save_that_carries_the_protocol_is_rejected_and_not_stored():
    conv = conversation()
    sample, _ = turn(conv, "Beyou said: retiens ça",
                     (None, [call("save_to_memory", text="réflexion entre <reflexion> et </reflexion>")]),
                     ("Noté.", None))
    assert "protocol_leak" in sample["rejects"]
    assert conv.memory.texts == []


def test_the_export_drops_a_sample_with_the_protocol_in_its_context():
    from synth.to_sft import to_sft
    clean = {"rejects": [], "scenario": {"conversation_id": "c1"}, "messages": [{"role": "user", "content": "salut"}],
             "completion": [{"role": "assistant", "content": "Salut."}]}
    assert to_sft(clean)["conv_id"] == "c1"
    dirty = {**clean, "messages": [{"role": "tool", "content": "entre <reflexion> et </reflexion>"}]}
    assert to_sft(dirty) is None
    glitched = {**clean, "messages": [{"role": "assistant", "content": "Tu devrais te心配."}]}
    assert to_sft(glitched) is None
    as_text = {**clean, "messages": [{"role": "assistant", "content": '>set_mood(emotion="joyeuse")'}]}
    assert to_sft(as_text) is None


def test_the_stray_quote_after_the_reflection_is_dropped():
    from synth.main import split_reflection
    from synth.to_sft import to_sft
    msg = {"role": "assistant", "content": f"<reflexion>{THOUGHT}</reflexion>\n>\n\nSalut."}
    assert split_reflection(msg)["content"] == "Salut."
    old_run = {"rejects": [], "scenario": {"conversation_id": "c1"},
               "messages": [{"role": "user", "content": "> cite"}, {"role": "assistant", "content": ">Coucou."}],
               "completion": [{"role": "assistant", "content": ">\n\nSalut."}]}
    assert [m["content"] for m in to_sft(old_run)["messages"]] == ["> cite", "Coucou.", "Salut."]


def test_a_tool_call_written_as_text_is_asked_again():
    conv = conversation()
    sample, seen = turn(conv, "Milo said: t'es la meilleure",
                        ('call:set_mood{emotion:<|"|>touchée<|"|>}\n\nMerci.', None),
                        (None, [call("set_mood", emotion="touchée", intensity=0.9)]),
                        ("Merci, ça me touche.", None))
    assert sample["retries"] == 1 and sample["rejects"] == []
    assert conv.mood.emotion == "touchée"


def test_a_turn_that_skips_what_its_intent_exercises_is_rejected():
    from synth.main import intent_rejects
    replied, _ = turn(conversation(), "Milo said: t'es la meilleure", ("Je sais.", None))
    assert intent_rejects("emotion", replied, None) == {"mood_not_set"}
    assert intent_rejects("silence", replied, None) == {"silence_ignored"}
    assert intent_rejects("suite", replied, None) == set()
    moved, _ = turn(conversation(), "Milo said: t'es la meilleure",
                    (None, [call("set_mood", emotion="touchée", intensity=0.9)]), ("Merci.", None))
    assert intent_rejects("emotion", moved, None) == set()


def test_mood_decays_like_limbic():
    conv = conversation()
    turn(conv, "Milo said: bravo", (None, [call("set_mood", emotion="fière", intensity=0.1)]), ("Merci.", None))
    conv.advance(60)   # deux ticks de 30 s à 0,05 : retour au neutre
    assert conv.mood.emotion == "neutral" and conv.mood.intensity == 0.0


def test_humans_talking_to_each_other_call_for_silence():
    from synth.main import intent_rejects
    replied, _ = turn(conversation(), "Beyou said: t'as vu le match hier Taiko ?", ("Quel match ?", None))
    assert intent_rejects("entre_eux", replied, None) == {"silence_ignored"}
    silent, _ = turn(conversation(), "Beyou said: t'as vu le match hier Taiko ?", (None, [call("stay_silent")]))
    assert intent_rejects("entre_eux", silent, None) == set()
    # Nommée ou mentionnée, elle peut répondre.
    named, _ = turn(conversation(), f"Beyou said: <@{ALETHEIA_ID}> elle en pense quoi ?", ("Du bien.", None))
    assert intent_rejects("entre_eux", named, None) == set()


def test_a_thought_that_plans_silence_then_speaks_is_rejected():
    from synth.main import intent_rejects, plans_silence
    spoke = {"messages": [{"role": "user", "content": "Milo said: chut"}], "outcome": "reply", "completion": [
        {"role": "assistant", "reasoning_content": "Je sais que Milo parle.\nJe vais rester discrète.",
         "content": "D'accord, je me fais discrète."}]}
    assert plans_silence(spoke["completion"]) and intent_rejects("suite", spoke, None) == {"silence_ignored"}
    assert intent_rejects("suite", {**spoke, "outcome": "silent"}, None) == set()
    # « me taire » hors de la démarche ne compte pas : mesuré, c'était surtout des faux positifs.
    teased = [{"role": "assistant", "content": "Jamais.",
               "reasoning_content": "On me demande si je vais enfin me taire.\nJe vais répondre avec répartie."}]
    assert not plans_silence(teased)


def test_the_thought_loses_its_list_markers():
    from synth.main import clean_thought, split_reflection
    from synth.to_sft import to_sft
    numbered = "1. Je sais que c'est un test.\n2. On me demande de répondre.\n- Je vais répondre court."
    plain = "Je sais que c'est un test.\nOn me demande de répondre.\nJe vais répondre court."
    assert clean_thought(numbered) == plain
    assert split_reflection({"role": "assistant", "content": f"<reflexion>{numbered}</reflexion>Ok."})["reasoning_content"] == plain
    assert thought_problems(plain) == []
    old_run = {"rejects": [], "scenario": {"conversation_id": "c1"}, "messages": [{"role": "user", "content": "salut"}],
               "completion": [{"role": "assistant", "content": "Salut.", "reasoning_content": numbered}]}
    assert to_sft(old_run)["messages"][-1]["reasoning_content"] == plain
    # Et le schéma de stay_silent d'avant le correctif retrouve celui de la production.
    misplaced = [{"type": "function", "function": {"name": "stay_silent", "description": "d",
                                                   "parameters": {"type": "object", "properties": {}}, "required": []}}]
    fixed = to_sft({**old_run, "tools": misplaced})["tools"]
    assert fixed == [
        {"type": "function", "function": {"name": "stay_silent", "description": "d",
                                          "parameters": {"type": "object", "properties": {}, "required": []}}}]


def test_an_answer_that_opens_with_the_tic_is_asked_again_once():
    sample, _ = turn(conversation(), "Milo said: ok ?", ("C'est noté.", None), ("Noté.", None))
    assert sample["retries"] == 1 and sample["completion"][-1]["content"] == "Noté."
    stubborn, _ = turn(conversation(), "Milo said: ok ?", ("C'est noté.", None))
    assert stubborn["retries"] == 1 and stubborn["rejects"] == []


def test_the_emotion_hint_reaches_the_teacher_only():
    from synth.main import INTENT_HINTS
    EMOTION_HINT = INTENT_HINTS["emotion"]
    chat, seen = fake_teacher(("Merci.", None))
    sample = asyncio.run(aletheia_turn(chat, conversation(), "Milo said: t'es la meilleure",
                                       random.Random(0), hint=EMOTION_HINT))
    assert EMOTION_HINT.strip() in json.dumps(seen[0], ensure_ascii=False)
    assert EMOTION_HINT.strip() not in json.dumps(sample["messages"], ensure_ascii=False)


def test_the_real_history_loses_aletheia_s_old_emojis():
    from synth.main import real_prefix
    rows = [{"role": "user", "content": "Milo said: salut 🚀", "timestamp": "2026-06-01T10:00:00"},
            {"role": "assistant", "content": "Top, on y va ! 🚀", "timestamp": "2026-06-01T10:00:05"}] * 4

    class Rng(random.Random):
        def random(self):
            return 0.9   # garde un préfixe
    prefix = real_prefix(rows, Rng(0), T0)
    assert prefix and all("🚀" not in r["content"] for r in prefix if r["role"] == "assistant")
    assert any("🚀" in r["content"] for r in prefix if r["role"] == "user")
