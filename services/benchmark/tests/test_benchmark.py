import pytest
import os
import tempfile
import json
from unittest.mock import MagicMock
from main import format_duration, draw_horizontal_bar, BenchmarkGraph

def test_format_duration():
    assert format_duration(0.0005) == "500 µs"
    assert format_duration(0.001) == "1.0 ms"
    assert format_duration(0.5) == "500.0 ms"
    assert format_duration(1.0) == "1.00 s"
    assert format_duration(2.5) == "2.50 s"

def test_benchmark_graph_loading():
    graph_data = {
        "name": "Test Graph",
        "description": "A graph for testing",
        "ingress_points": [
            {"topic": "input.topic", "service": "test_service", "description": "start"}
        ],
        "steps": [
            {
                "id": "start",
                "name": "Start Step",
                "description": "Starting point",
                "topics": ["input.topic"],
                "display_color": "green"
            },
            {
                "id": "middle",
                "name": "Middle Step",
                "description": "Middle point",
                "topics": ["middle.topic"],
                "filter": "payload.get('value') == 42",
                "display_color": "yellow"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(graph_data, f)
        temp_path = f.name

    try:
        graph = BenchmarkGraph(temp_path)
        assert graph.name == "Test Graph"
        assert graph.description == "A graph for testing"
        assert len(graph.ingress_points) == 1
        assert len(graph.steps) == 2
        assert "input.topic" in graph.topics
        assert "middle.topic" in graph.topics
    finally:
        os.remove(temp_path)

def test_find_step_for_event():
    graph_data = {
        "name": "Test Graph",
        "ingress_points": [{"topic": "input.topic"}],
        "steps": [
            {
                "id": "start",
                "name": "Start",
                "topics": ["input.topic"]
            },
            {
                "id": "middle",
                "name": "Middle",
                "topics": ["middle.topic"],
                "filter": "payload.get('value') == 42"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(graph_data, f)
        temp_path = f.name

    try:
        graph = BenchmarkGraph(temp_path)
        
        # Test topic match without filter
        step = graph.find_step_for_event("input.topic", {})
        assert step is not None
        assert step["id"] == "start"

        # Test topic match with filter evaluating True
        step = graph.find_step_for_event("middle.topic", {"value": 42})
        assert step is not None
        assert step["id"] == "middle"

        # Test topic match with filter evaluating False
        step = graph.find_step_for_event("middle.topic", {"value": 100})
        assert step is None

        # Test topic mismatch
        step = graph.find_step_for_event("unknown.topic", {})
        assert step is None
    finally:
        os.remove(temp_path)

def test_draw_horizontal_bar():
    segments = [
        {"name": "Step 1", "duration": 0.1, "style": "bold blue"},
        {"name": "Step 2", "duration": 0.2, "style": "bold green"},
    ]
    bar = draw_horizontal_bar(80, segments)
    assert bar is not None
    # Rich text representations
    assert "█" in bar.plain
