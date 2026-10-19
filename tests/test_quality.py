"""质量评分单元测试（离线，纯函数）。"""
import pytest

from sqa.quality.anchors import score_anchor
from sqa.quality.engine import adjust_grade, map_grade
from sqa.quality.metrics import (
    cagr, cagr_last_n, cumulative_ratio, days_change_pct, median,
)
from sqa.quality.weights import (
    DIMENSIONS, STYLE_WEIGHTS, resolve_weights,
)


# ---------- 锚点插值 ----------

def test_anchor_normal_interpolation():
    assert score_anchor(8, 3, 8, 18) == 50.0        # fifty 点
    assert score_anchor(3, 3, 8, 18) == 0.0
    assert score_anchor(18, 3, 8, 18) == 100.0
    assert score_anchor(13, 3, 8, 18) == 75.0       # 中点线性
    assert score_anchor(25, 3, 8, 18) == 100.0      # 超出截断


def test_anchor_reverse_interpolation():
    # 反向锚点：质押率 50→0分, 20→50分, 5→100分
    assert score_anchor(20, 50, 20, 5) == 50.0
    assert score_anchor(50, 50, 20, 5) == 0.0
    assert score_anchor(5, 50, 20, 5) == 100.0
    assert score_anchor(2, 50, 20, 5) == 100.0      # 更低也截断到 100
    assert score_anchor(60, 50, 20, 5) == 0.0


# ---------- 权重表 ----------

def test_all_style_weights_sum_to_100():
    for style, weights in STYLE_WEIGHTS.items():
        assert sum(weights.values()) == 100.0, style


def test_dividend_overlay_floors_and_normalization():
    weights, applied = resolve_weights("cyclical", dividend_badge=True)
    assert applied is True
    assert weights["governance"] >= 25.0
    assert weights["cashflow"] >= 20.0
    assert sum(weights.values()) == pytest.approx(100.0)


def test_no_overlay_without_badge():
    weights, applied = resolve_weights("value", dividend_badge=False)
    assert applied is False
    assert weights == STYLE_WEIGHTS["value"]


def test_skip_dimension_redistribution():
    # 周期股 growth SKIP：权重摊给其余维度，合计仍 100
    weights, _ = resolve_weights("cyclical", dividend_badge=False, skip_dims={"growth"})
    assert weights["growth"] == 0.0
    assert sum(weights.values()) == pytest.approx(100.0)
    assert weights["health"] > STYLE_WEIGHTS["cyclical"]["health"]


# ---------- 度量 ----------

def test_median_and_cagr():
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([4.0, 1.0, 2.0, 3.0]) == 2.5
    assert cagr([100, 110, 121, 133.1]) == pytest.approx(0.10)  # 恰好 10%
    assert cagr([100, None, None]) is None          # 点数不足
    assert cagr([-100, 50, 60, 70]) is None         # 基期为负


def test_cagr_last_n_uses_only_recent_3y():
    # 前 6 年高增长、最近 3 年低增长：全序列 CAGR 远高于 3 年 CAGR
    values = [100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0,
              3400.0, 3500.0, 3600.0, 3700.0]
    full = cagr(values)
    recent = cagr_last_n(values, 3)
    assert full > 0.30
    assert recent == pytest.approx((3700.0 / 3400.0) ** (1 / 3) - 1)
    assert recent < 0.05


def test_cagr_last_n_requires_complete_window():
    assert cagr_last_n([100, 110, 121, 133.1], 3) == pytest.approx(0.10)
    assert cagr_last_n([100, 110, None, 121, 133.1], 3) is None
    assert cagr_last_n([100, 110, 121], 3) is None
    assert cagr_last_n([-100, 10, 20, 30], 3) is None


def test_cumulative_ratio():
    assert cumulative_ratio([10, 20], [20, 30]) == 30 / 50
    assert cumulative_ratio([10, None], [20, 30]) == 0.5
    assert cumulative_ratio([], []) is None


def test_days_change_pct():
    # 周转率 2.0→1.0（天数翻倍）→ 恶化 100%
    assert abs(days_change_pct([2.0, 2.0, 2.0, 1.0], 3) - 100.0) < 1e-9
    # 改善：1.0→1.5（天数 -33.3%）
    v = days_change_pct([1.0, 1.0, 1.0, 1.5], 3)
    assert v < 0 and abs(v + 33.33) < 0.1


# ---------- 评级 ----------

def test_grade_bands():
    assert map_grade(90) == "A+"
    assert map_grade(80) == "A"
    assert map_grade(70) == "B"
    assert map_grade(50) == "C"
    assert map_grade(30) == "D"


def test_grade_adjustment_by_minesweeper():
    assert adjust_grade("A", "pass") == "A"
    assert adjust_grade("A", "degraded") == "B"     # 中风险降一档
    assert adjust_grade("A", "blocked") == "C"      # 强制分析的高风险双降
    assert adjust_grade("D", "blocked") == "D"      # 已到底不越界


def test_dimensions_tuple_covers_all():
    assert set(DIMENSIONS) == set(STYLE_WEIGHTS["growth"].keys())
