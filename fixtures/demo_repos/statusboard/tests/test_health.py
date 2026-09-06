"""The suite that already existed. The agent must never break these."""
from statusboard.health import Sample, classify, overall, p95_latency, uptime_pct


def test_a_fast_sample_is_healthy():
    assert classify(Sample("/api", 120)) == "healthy"


def test_a_slow_sample_is_degraded():
    assert classify(Sample("/api", 900)) == "degraded"


def test_a_failed_sample_is_down():
    assert classify(Sample("/api", 100, ok=False)) == "down"


def test_a_very_slow_sample_is_down():
    assert classify(Sample("/api", 6000)) == "down"


def test_uptime_of_a_clean_window():
    samples = [Sample("/api", 100) for _ in range(10)]
    assert uptime_pct(samples) == 100.0


def test_uptime_counts_the_outage():
    samples = [Sample("/api", 100) for _ in range(9)] + [Sample("/api", 100, ok=False)]
    assert uptime_pct(samples) == 90.0


def test_uptime_of_an_empty_window():
    assert uptime_pct([]) == 100.0


def test_p95_picks_the_slow_tail():
    samples = [Sample("/api", ms) for ms in range(1, 101)]
    assert p95_latency(samples) == 95


def test_overall_reports_the_worst_sample():
    assert overall([Sample("/api", 100), Sample("/api", 900)]) == "degraded"
    assert overall([Sample("/api", 100), Sample("/api", 100)]) == "healthy"
