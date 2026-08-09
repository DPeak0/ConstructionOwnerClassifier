from owner_classifier.models import AppSettings
from owner_classifier.performance import effective_concurrency, recommended_concurrency


def test_recommended_concurrency_is_bounded_by_cpu_and_memory():
    gib = 1024 ** 3
    assert recommended_concurrency(cpu_count=2, memory_bytes=32 * gib) == 1
    assert recommended_concurrency(cpu_count=8, memory_bytes=16 * gib) == 2
    assert recommended_concurrency(cpu_count=16, memory_bytes=8 * gib) == 2
    assert recommended_concurrency(cpu_count=32, memory_bytes=32 * gib) == 4


def test_effective_concurrency_supports_auto_and_manual(monkeypatch):
    monkeypatch.setattr("owner_classifier.performance.recommended_concurrency", lambda: 3)
    assert effective_concurrency(AppSettings(concurrency=1, concurrency_auto=True)) == 3
    assert effective_concurrency(AppSettings(concurrency=4, concurrency_auto=False)) == 4
