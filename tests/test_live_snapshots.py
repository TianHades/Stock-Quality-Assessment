"""真实数据快照回归：风险闸门 / 质量 / 估值 / 3.5/5.8 校准。

默认跳过；开启方式：
    SQA_INTEGRATION=1 pytest tests/test_live_snapshots.py -v

不追求逐分精确（数据会更新），锁定"结论方向 + 关键规则行为"，防止后续
改动静默改变风险画像。

2026-10-25 H 步：股息率 D1 依赖 10Y 国债动态阈值，快照回归固定
RISK_FREE_RATE=0.015（与 golden 口径一致，docs/01 校准记录建议）；
同时按 H 步规则修正（2.1/3.3/4.1/4.4/4.5 口径）重新锁定风险画像。
"""
from __future__ import annotations

import os

import pytest

# 必须在 load_thresholds/load_dotenv 之前写入：固定无风险利率快照，避免
# 实时国债/默认值漂移导致 D1 红利标签回归不稳定（docs/01 校准记录）。
os.environ.setdefault("RISK_FREE_RATE", "0.015")

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
    # 2026-10-25 复核：4.4 改税前营业利润口径（茅台税后净利比较曾虚报 WARN），7→4
    assert moutai["total_score"] == 4
    assert _rule(moutai, "3.5")["verdict"] == "SKIP"   # 应收账款极小，不比较
    assert _rule(moutai, "5.8")["verdict"] == "PASS"

    shenhua = run_minesweeper("601088")
    assert shenhua["verdict"] == "degraded"
    assert shenhua["total_score"] == 14
    assert _rule(shenhua, "3.5")["verdict"] == "FAIL"  # 同行加权坏账率显著更高

    midea = run_minesweeper("000333")
    # H 步：2.1/4.1/4.4/4.5 口径修正后，美的历史期现金流/核心利润信号不再触发，
    # 由 degraded(23) → pass；2026-10-25 复核 4.4 税基修正后 7→4；4.2/5.8 保留 WARN。
    assert midea["verdict"] == "pass"
    assert midea["total_score"] == 4
    assert _rule(midea, "3.5")["verdict"] == "PASS"
    assert _rule(midea, "5.8")["verdict"] == "WARN"    # 0.5%/YoY54%，未到 1% FAIL 线

    boe = run_minesweeper("000725")
    # H 步：4.4 FAIL→WARN（补扣研发，−4）、4.5 FAIL→PASS（近5年窗口，−7）；
    # 2026-10-25 复核 4.4 税基修正 WARN→PASS（−3）；1.6/2.1 仍 FAIL、3.3 新旧均 WARN；
    # 31 → 20 → 17，仍为 degraded。
    assert boe["verdict"] == "degraded"
    assert boe["total_score"] == 17
    assert _rule(boe, "1.6")["verdict"] == "FAIL"
    assert _rule(boe, "2.1")["verdict"] == "FAIL"

    yangtze = run_minesweeper("600900")
    # H 步：2.1 由 WARN(3) → PASS（capex 口径，−3）；其余 3.5/5.1/5.2/S2 等 FAIL
    # 不变；26 → 23，由 blocked 降为 degraded（仍非 pass）。
    assert yangtze["verdict"] == "degraded"
    assert yangtze["total_score"] == 23
    assert _rule(yangtze, "3.5")["verdict"] == "FAIL"
