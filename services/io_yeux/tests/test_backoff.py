import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import backoff


def test_no_wait_without_failure():
    assert backoff(0) == 0.0


def test_the_wait_doubles_from_5_seconds_up_to_60():
    assert [backoff(n) for n in range(1, 8)] == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0]


def test_the_wait_never_exceeds_60_seconds_after_a_long_outage():
    assert backoff(1000) == 60.0
