import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.stream import consume_stream


def chunk(content=None, tool_calls=None, finish_reason=None, usage=None, choices=True):
    """Reproduit la forme d'un chunk du SDK OpenAI (accès par attributs)."""
    if not choices:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=usage,
    )


def tool_chunk(index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def fake_clock(ticks):
    """Horloge fictive : un tick consommé par chunk, puis la dernière valeur en boucle."""
    remaining = list(ticks)
    last = [remaining[-1] if remaining else 0.0]

    def clock():
        if remaining:
            last[0] = remaining.pop(0)
        return last[0]

    return clock


async def as_stream(chunks):
    for c in chunks:
        yield c


def run(chunks, ticks, start=0.0):
    # ponytail: asyncio.run plutôt que pytest-asyncio — la couture est la seule
    # chose async ici, pas la peine d'un plugin pour ça.
    return asyncio.run(consume_stream(as_stream(chunks), start, clock=fake_clock(ticks)))


def test_ttft_and_ttff_differ_when_the_first_sentence_is_long():
    result = run(
        [chunk("Bonjour "), chunk("tout "), chunk("le "), chunk("monde.")],
        ticks=[0.2, 0.3, 0.4, 0.5],
    )
    # Le premier token arrive à 0.2, mais la voix ne part qu'au premier point.
    assert result["ttft"] == pytest.approx(0.2)
    assert result["ttff"] == pytest.approx(0.5)
    assert result["fragments"] == ["Bonjour tout le monde."]
    assert result["text"] == "Bonjour tout le monde."


def test_empty_chunks_and_chunks_without_choices_are_ignored():
    result = run(
        [chunk(choices=False), chunk(""), chunk("Salut."), chunk(None), chunk("")],
        ticks=[0.1, 0.2, 0.3, 0.4, 0.5],
    )
    # Aucun de ces vides ne doit avancer le TTFT ni casser le comptage.
    assert result["ttft"] == pytest.approx(0.3)
    assert result["ttff"] == pytest.approx(0.3)
    assert result["completion_tokens"] == 1
    assert result["fragments"] == ["Salut."]


def test_tool_call_split_across_chunks_is_reassembled():
    result = run(
        [
            chunk(tool_calls=[tool_chunk(id="call_1", name="save_to", arguments='{"te')]),
            chunk(tool_calls=[tool_chunk(name="_memory", arguments='xt": "il ')]),
            chunk(tool_calls=[tool_chunk(arguments='aime le café"}')]),
            chunk(finish_reason="tool_calls"),
        ],
        ticks=[0.1, 0.2, 0.3, 0.4],
    )
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "save_to_memory", "arguments": '{"text": "il aime le café"}'}
    ]
    assert result["finish_reason"] == "tool_calls"
    # Aucun texte n'a été streamé : pas de TTFT/TTFF inventé.
    assert result["ttft"] is None
    assert result["ttff"] is None


def test_trailing_text_before_a_tool_call_is_not_counted_as_a_fragment():
    result = run(
        [
            chunk("Laisse-moi vérifier"),
            chunk(tool_calls=[tool_chunk(id="c", name="get_from_memory", arguments="{}")]),
            chunk(finish_reason="tool_calls"),
        ],
        ticks=[0.2, 0.3, 0.4],
    )
    # main.py garde ce reste pour le tour suivant au lieu de le publier : le
    # flusher ici donnerait un TTFF que la production ne produit jamais.
    assert result["fragments"] == []
    assert result["ttff"] is None
    assert result["ttft"] == pytest.approx(0.2)
    assert result["text"] == "Laisse-moi vérifier"


def test_parallel_tool_calls_stay_separated_by_index():
    result = run(
        [
            chunk(tool_calls=[
                tool_chunk(index=0, id="a", name="stay_silent", arguments="{}"),
                tool_chunk(index=1, id="b", name="set_mood", arguments='{"emo'),
            ]),
            chunk(tool_calls=[tool_chunk(index=1, arguments='tion": "joyeuse"}')]),
        ],
        ticks=[0.1, 0.2],
    )
    assert [tc["name"] for tc in result["tool_calls"]] == ["stay_silent", "set_mood"]
    assert result["tool_calls"][1]["arguments"] == '{"emotion": "joyeuse"}'


def test_stream_cut_before_the_end_still_flushes_what_was_received():
    result = run(
        [chunk("Attends"), chunk(", je")],
        ticks=[0.2, 0.4],
    )
    # Flux coupé sans ponctuation ni finish_reason : le reste part quand même,
    # et le TTFF est la fin du flux — c'est là que la voix démarrerait.
    assert result["fragments"] == ["Attends, je"]
    assert result["finish_reason"] is None
    assert result["ttft"] == pytest.approx(0.2)
    assert result["ttff"] == pytest.approx(0.4)


def test_response_without_any_punctuation_has_ttff_at_end_of_stream():
    result = run(
        [chunk("oui"), chunk(" bien"), chunk(" sûr")],
        ticks=[0.1, 0.2, 0.3, 0.9],
    )
    assert result["ttft"] == pytest.approx(0.1)
    assert result["ttff"] == pytest.approx(0.9)
    assert result["fragments"] == ["oui bien sûr"]


def test_throughputs_come_from_usage_when_the_server_reports_it():
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=40)
    result = run(
        [chunk("Voilà."), chunk(" Fini."), chunk(choices=False, usage=usage)],
        ticks=[0.5, 1.0, 1.0],
    )
    assert result["prompt_tokens"] == 1000
    assert result["completion_tokens"] == 40
    assert result["prefill_tps"] == pytest.approx(2000.0)   # 1000 tokens / 0.5s
    assert result["decode_tps"] == pytest.approx(80.0)      # 40 tokens / 0.5s


def test_single_token_response_reports_no_decode_rate():
    result = run([chunk("Non.")], ticks=[0.3])
    # Un seul token : la fenêtre de décodage est nulle, un débit serait une division par zéro.
    assert result["decode_tps"] is None
    assert result["prompt_tokens"] is None
    assert result["prefill_tps"] is None


def test_quality_predicates_flag_english_and_leaked_system_tags():
    fr = run([chunk("Je ne sais pas ce que tu veux dire.")], ticks=[0.1])
    assert fr["french"] is True
    assert fr["xml_leak"] is False

    en = run([chunk("I do not know what you want me to say.")], ticks=[0.1])
    assert en["french"] is False

    leak = run([chunk("<persona>\nJe suis Aletheia.")], ticks=[0.1])
    assert leak["xml_leak"] is True
