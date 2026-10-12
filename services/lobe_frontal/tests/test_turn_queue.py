import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.turn_queue import is_for_turn, take_latest


def msg(name, source=None, stale=False):
    return {"prompt": name, "source": source, "_stale": stale}


def test_the_last_user_message_wins_and_the_others_go_to_memory():
    a, b, c = msg("A"), msg("B"), msg("C")
    assert take_latest([a, b, c]) == ([a, b], c)


def test_a_single_message_is_answered():
    a = msg("A")
    assert take_latest([a]) == ([], a)


def test_a_user_message_wins_over_a_later_proactive_prompt():
    user, proactive = msg("A"), msg("ennui", source="proactive")
    assert take_latest([user, proactive]) == ([], user)


def test_a_proactive_prompt_never_goes_to_memory_as_skipped():
    proactive, user = msg("ennui", source="proactive"), msg("A")
    assert take_latest([proactive, user]) == ([], user)


def test_a_fresh_proactive_prompt_alone_is_answered():
    proactive = msg("ennui", source="proactive")
    assert take_latest([proactive]) == ([], proactive)


def test_a_proactive_prompt_that_waited_is_dropped():
    # Un tour a eu lieu pendant l'attente : l'ennui ne tient plus.
    assert take_latest([msg("ennui", source="proactive", stale=True)]) == ([], None)


def test_speech_events_match_the_turn_by_correlation_id():
    assert is_for_turn({"correlation_id": "c1"}, "c1")
    assert not is_for_turn({"correlation_id": "c0"}, "c1")
    assert is_for_turn({}, "c1")
