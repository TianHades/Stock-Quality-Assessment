"""真实数据快照回归：风险闸门 / 质量 / 估值 / 3.5/5.8 校准。

默认跳过；开启方式：
    SQA_INTEGRATION=1 pytest tests/test_live_snapshots.py -v

不追求逐分精确（数据会更新），锁定"结论方向 + 关键规则行为"，防止后续
改动静默改变风险画像。
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SQA_INTEGRATION") != "1",
        reason="需要网络与 token，设置 SQA_INTEGRATION=1 开启",
    ),
]


def _rule(result: dict, rule_id: str) -> dict:
    return next(r for r in result["rules"] if r["id"] == rule_id)


def test_quality_and_valuation_snapshots():
    from sqa.analyze import run_analyze

    moutai = run_analyze("600519")
    assert moutai["pipeline"] == "complete"
    assert moutai["classification"]["style"] == "balanced"
    assert "dividend" in moutai["classification"]["badges"]
    assert moutai["minesweeper"]["verdict"] == "pass"
    assert moutai["quality"]["grade"] == "A"
    assert moutai["valuation"]["verdict"] == "undervalued"
    assert "dividend_sustainability" in moutai["modules"]

    shenhua = run_analyze("601088")
    assert shenhua["pipeline"] == "complete"
    assert shenhua["classification"]["style"] == "cyclical"
    assert shenhua["minesweeper"]["verdict"] == "degraded"
    assert 11 <= shenhua["minesweeper"]["total_score"] <= 25
    assert shenhua["quality"]["grade"] == "A+"
    assert shenhua["quality"]["grade_adjusted"] == "A"
    assert shenhua["valuation"]["verdict"] == "overvalued"
    assert "cycle_position" in shenhua["modules"]


def test_risk_gate_and_peer_calibration_snapshots():
    from sqa.minesweeper.engine import run_minesweeper

    moutai = run_minesweeper("600519")
    assert moutai["verdict"] == "pass"
    assert _rule(moutai, "3.5")["verdict"] == "SKIP"   # 应收账款极小，不比较
    assert _rule(moutai, "5.8")["verdict"] == "PASS"

    shenhua = run_minesweeper("601088")
    assert shenhua["verdict"] == "degraded"
    assert _rule(shenhua, "3.5")["verdict"] == "FAIL"  # 同行加权坏账率显著更高

    midea = run_minesweeper("000333")
    assert midea["verdict"] == "degraded"
    assert 20 <= midea["total_score"] <= 30
    assert _rule(midea, "3.5")["verdict"] == "PASS"
    assert _rule(midea, "5.8")["verdict"] == "WARN"    # 0.5%/YoY54%，未到 1% FAIL 线

    boe = run_minesweeper("000725")
    assert boe["verdict"] == "blocked" and boe["forceable"] is True

    yangtze = run_minesweeper("600900")
    assert yangtze["verdict"] == "blocked" and yangtze["forceable"] is True
