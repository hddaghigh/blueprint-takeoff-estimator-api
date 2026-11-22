"""Keeps scripts/benchmark.py working and the synthetic suite at 100%."""

from scripts import benchmark


def test_synthetic_benchmark_is_perfect():
    result = benchmark.run()

    assert set(result["summary"]) == {"doors", "flooring", "walls"}
    for category, stats in result["summary"].items():
        assert stats["precision"] == 1.0, (category, stats)
        assert stats["recall"] == 1.0, (category, stats)
    assert len(result["cases"]) >= 10
