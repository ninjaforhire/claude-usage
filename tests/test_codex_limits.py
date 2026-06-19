"""Tests for the Codex rate-limit reader (5h + weekly orbs)."""

import json

import codex_limits


def _rollout(path, snapshots):
    """Write a rollout JSONL with rate_limits records (+ noise lines)."""
    lines = [json.dumps({"type": "message", "text": "noise"})]
    for snap in snapshots:
        lines.append(json.dumps({"type": "token_count", "rate_limits": snap}))
    path.write_text("\n".join(lines))


def _snap(primary_used, secondary_used):
    return {
        "limit_id": "codex", "plan_type": "prolite",
        "primary": {"used_percent": primary_used, "window_minutes": 300, "resets_at": 1781508257},
        "secondary": {"used_percent": secondary_used, "window_minutes": 10080, "resets_at": 1781763453},
    }


def test_maps_primary_to_5h_and_secondary_to_weekly(tmp_path):
    d = tmp_path / "2026" / "06" / "15"
    d.mkdir(parents=True)
    _rollout(d / "rollout-2026-06-15T01-30-08-aaa.jsonl", [_snap(5.0, 11.0)])
    out = codex_limits.codex_orb_data(sessions_dir=tmp_path)
    assert out["error"] is None
    assert out["plan_type"] == "prolite"
    assert out["windows"]["five_hour"]["remaining_pct"] == 95
    assert out["windows"]["seven_day"]["remaining_pct"] == 89
    assert out["windows"]["five_hour"]["resets_at"].startswith("2026-")


def test_last_snapshot_in_file_wins(tmp_path):
    d = tmp_path / "2026" / "06" / "15"
    d.mkdir(parents=True)
    _rollout(d / "rollout-2026-06-15T01-30-08-aaa.jsonl", [_snap(5.0, 11.0), _snap(40.0, 60.0)])
    out = codex_limits.codex_orb_data(sessions_dir=tmp_path)
    assert out["windows"]["five_hour"]["remaining_pct"] == 60
    assert out["windows"]["seven_day"]["remaining_pct"] == 40


def test_newest_file_wins_across_sessions(tmp_path):
    d = tmp_path / "2026" / "06" / "15"
    d.mkdir(parents=True)
    _rollout(d / "rollout-2026-06-15T01-00-00-aaa.jsonl", [_snap(5.0, 5.0)])
    _rollout(d / "rollout-2026-06-15T09-00-00-bbb.jsonl", [_snap(80.0, 30.0)])
    out = codex_limits.codex_orb_data(sessions_dir=tmp_path)
    assert out["windows"]["five_hour"]["remaining_pct"] == 20  # 100 - 80, from the newer file


def test_colors_match_remaining_ramp(tmp_path):
    d = tmp_path / "2026" / "06" / "15"
    d.mkdir(parents=True)
    _rollout(d / "rollout-2026-06-15T01-30-08-aaa.jsonl", [_snap(95.0, 0.0)])  # 5% remaining = red zone
    w = codex_limits.codex_orb_data(sessions_dir=tmp_path)["windows"]
    assert w["five_hour"]["color_hi"] == accounts_red()
    assert w["seven_day"]["remaining_pct"] == 100


def accounts_red():
    import accounts
    return accounts.remaining_color(5)[0]


def test_no_data_returns_error(tmp_path):
    out = codex_limits.codex_orb_data(sessions_dir=tmp_path)
    assert out["error"] is not None
    assert out["windows"] == {}


# ── PLAN_CAPS tests: pro-5x vs chatgpt-pro ────────────────────────────────────

def test_plan_caps_has_pro_5x():
    """pro-5x tier must exist in PLAN_CAPS."""
    assert "pro-5x" in codex_limits.PLAN_CAPS


def test_plan_caps_has_chatgpt_pro():
    """chatgpt-pro tier must exist in PLAN_CAPS so we can compare."""
    assert "chatgpt-pro" in codex_limits.PLAN_CAPS


def test_pro_5x_monthly_is_100():
    """pro-5x subscription is $100/mo, not $200 (chatgpt-pro)."""
    assert codex_limits.PLAN_CAPS["pro-5x"]["monthly_usd"] == 100


def test_chatgpt_pro_monthly_is_200():
    """chatgpt-pro is $200/mo — distinct from pro-5x."""
    assert codex_limits.PLAN_CAPS["chatgpt-pro"]["monthly_usd"] == 200


def test_pro_5x_and_chatgpt_pro_caps_differ():
    """pro-5x (5x Plus) caps must differ from chatgpt-pro (unlimited-ish) caps."""
    p5x = codex_limits.PLAN_CAPS["pro-5x"]
    pro = codex_limits.PLAN_CAPS["chatgpt-pro"]
    assert p5x != pro, "pro-5x and chatgpt-pro must have different cap structures"


def test_pro_5x_five_hour_is_5x_plus():
    """pro-5x five_hour_limit_h should be 5x the Plus (chatgpt-plus) tier."""
    plus = codex_limits.PLAN_CAPS.get("chatgpt-plus", {})
    p5x = codex_limits.PLAN_CAPS["pro-5x"]
    if plus and plus.get("five_hour_limit_h") is not None:
        assert p5x["five_hour_limit_h"] == 5 * plus["five_hour_limit_h"]


def test_plan_caps_get_returns_pro_5x_for_current_plan():
    """get_plan_caps('pro-5x') returns the pro-5x caps, not pro caps."""
    caps = codex_limits.get_plan_caps("pro-5x")
    pro_caps = codex_limits.get_plan_caps("chatgpt-pro")
    assert caps["monthly_usd"] == 100
    assert caps != pro_caps
