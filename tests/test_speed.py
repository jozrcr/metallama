"""Tests for metallama.app.speed — log-line parser for tokens-per-second."""
from metallama.app.speed import feed_line, latest_speeds, clear_speed


def test_parse_decoded_line():
    feed_line("m", "decoded 1234 tokens in 12.34 seconds, 100.12 tokens per second")
    assert latest_speeds["m"]["gen_tps"] == 100.12
    assert latest_speeds["m"]["pp_tps"] is None


def test_parse_prompt_eval_line():
    feed_line("m2", "prompt eval: 567 tokens in 1.23 seconds, 460.98 tokens per second")
    assert latest_speeds["m2"]["pp_tps"] == 460.98
    assert latest_speeds["m2"]["gen_tps"] is None


def test_both_parsed_independently():
    feed_line("m3", "prompt eval: 100 tokens in 0.5 seconds, 200.0 tokens per second")
    feed_line("m3", "decoded 200 tokens in 4.0 seconds, 50.0 tokens per second")
    assert latest_speeds["m3"]["pp_tps"] == 200.0
    assert latest_speeds["m3"]["gen_tps"] == 50.0


def test_garbage_no_crash():
    latest_speeds.clear()
    feed_line("g", "some random log line with no numbers")
    feed_line("g", "")
    feed_line("g", "llama_server: listening on http://127.0.0.1:8080")
    # feed_line creates entry on first call even if no match — that's fine
    assert latest_speeds.get("g", {}).get("pp_tps") is None
    assert latest_speeds.get("g", {}).get("gen_tps") is None


def test_clear_speed():
    feed_line("c", "decoded 10 tokens in 1.0 seconds, 10.0 tokens per second")
    assert "c" in latest_speeds
    clear_speed("c")
    assert "c" not in latest_speeds
    clear_speed("nonexistent")  # should not raise
