import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Stats, vlm_metrics

RESPONSE = {
    "choices": [{"message": {"content": "{}"}}],
    "usage": {"prompt_tokens": 940, "completion_tokens": 120},
    "timings": {"prompt_n": 940, "prompt_ms": 334.5, "predicted_n": 120, "predicted_ms": 994.3},
}


def test_vlm_metrics_come_from_the_timings_of_llama_server():
    assert vlm_metrics(RESPONSE) == {"prefill_ms": 334.5, "generation_ms": 994.3, "prompt_tokens": 940}


def test_vlm_metrics_tolerate_a_server_without_timings():
    assert vlm_metrics({"choices": []}) == {"prefill_ms": 0.0, "generation_ms": 0.0, "prompt_tokens": 0}


def filled_stats() -> Stats:
    stats = Stats(started=0.0)
    for _ in range(60):
        stats.capture(capture_s=0.035, compare_s=0.004)
    stats.observation("change", vlm_s=1.2, metrics=vlm_metrics(RESPONSE), state_bytes=400)
    stats.observation("change", vlm_s=1.0, metrics=vlm_metrics(RESPONSE), state_bytes=200)
    stats.observation("heartbeat", vlm_s=1.1, metrics=vlm_metrics(RESPONSE), state_bytes=300)
    stats.republish(state_bytes=300)
    stats.suspended(12.0)
    stats.error()
    return stats


def test_the_summary_gives_every_metric():
    line = filled_stats().summary(now=60.0)
    assert "1.0 capture/s" in line
    assert "capture 35 ms" in line
    assert "comparaison 4 ms" in line
    assert "change 2/min" in line
    assert "heartbeat 1/min" in line
    assert "republication 1/min" in line
    assert "ignorées 95 %" in line  # 57 captures sur 60 sans appel VLM
    assert "VLM 1.1 s" in line
    assert "prefill 334 ms" in line
    assert "génération 994 ms" in line
    assert "940 jetons de prompt" in line
    assert "état 300 o" in line
    assert "suspendu 12 s" in line
    assert "erreurs 1" in line


def test_the_summary_without_activity_says_so_without_dividing_by_zero():
    line = Stats(started=0.0).summary(now=60.0)
    assert "0.0 capture/s" in line
    assert "aucune observation" in line
