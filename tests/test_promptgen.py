"""Behavior tests for promptgen model recommendation + prompt assembly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promptgen import recommend_model, build_prompt


def test_rogue_recommends_haiku():
    assert recommend_model({"pid": 42, "command": "claude -p"}) == "haiku"


def test_parse_error_recommends_sonnet():
    assert recommend_model({"label": "x", "parse_error": True}) == "sonnet"


def test_disabled_recommends_haiku():
    f = {"label": "x", "expected_state": "disabled", "reasons": ["last exit code 2"]}
    assert recommend_model(f) == "haiku"


def test_toggle_only_reasons_recommend_haiku():
    f = {"label": "x", "reasons": ["past eol (2020-01-01)"]}
    assert recommend_model(f) == "haiku"


def test_opus_hint_label_with_exit_recommends_opus():
    f = {"label": "com.mighty.mission-watchdog", "reasons": ["last exit code 2"]}
    assert recommend_model(f) == "opus"


def test_plain_exit_recommends_sonnet():
    f = {"label": "com.test.simple-script", "reasons": ["last exit code 1"]}
    assert recommend_model(f) == "sonnet"


def test_empty_findings_message():
    assert "No findings" in build_prompt([])


def test_prompt_lists_each_selected_entry():
    findings = [
        {"label": "com.a", "bucket": "WASTE", "reasons": ["last exit code 1"]},
        {"label": "com.b", "bucket": "UNKNOWN", "reasons": []},
    ]
    text = build_prompt(findings)
    assert "com.a" in text
    assert "com.b" in text
    assert "report-only" in text.lower()


def test_prompt_batch_summary_orders_cheapest_first():
    findings = [
        {"label": "com.mighty.pipeline", "reasons": ["last exit code 1"]},  # opus
        {"label": "com.simple", "reasons": ["last exit code 1"]},           # sonnet
        {"label": "com.dead", "expected_state": "disabled", "reasons": ["past eol"]},  # haiku
    ]
    text = build_prompt(findings)
    summary_line = next(l for l in text.splitlines() if "Batch by cost" in l)
    assert summary_line.index("haiku") < summary_line.index("sonnet") < summary_line.index("opus")
