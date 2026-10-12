import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import match_transaction


def txs(*ids):
    return {i: {"id": i} for i in ids}


def test_two_overlapping_requests_keep_their_own_events():
    active, seen = txs("A", "B"), set()
    # cortex.prompt of each request, in the order of arrival
    assert match_transaction(active, {"correlation_id": "a"}, seen)["id"] == "A"
    assert match_transaction(active, {"correlation_id": "b"}, seen)["id"] == "B"
    # A fragment of A after B arrived: it goes to A, not to the newest transaction.
    assert match_transaction(active, {"correlation_id": "a", "is_last": True}, seen)["id"] == "A"


def test_an_event_without_correlation_id_goes_to_the_newest_transaction():
    assert match_transaction(txs("A", "B"), {"sequence": 0}, set())["id"] == "B"


def test_a_late_event_of_a_finished_request_takes_no_transaction():
    active, seen = txs("A"), {"old"}
    assert match_transaction(active, {"correlation_id": "old"}, seen) is None
    assert "correlation_id" not in active["A"]


def test_no_transaction_gives_none():
    assert match_transaction({}, {"correlation_id": "a"}, set()) is None
