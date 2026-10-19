"""估值层单元测试（离线，纯函数）。"""
import pytest

from sqa.valuation.engine import (
    _pctl_current, cyclical_verdict, dividend_spread, dividend_verdict,
    growth_verdict, peg_of, percentile_verdict,
)


# ---------- 分位法 ----------

def test_pctl_current_requires_latest_trading_day_value():
    assert _pctl_current([20.0, 30.0]) == pytest.approx(1.0)
    # 最新交易日 PE 为 None（亏损）时不能拿旧值冒充当前值
    assert _pctl_current([20.0, 30.0, None, None]) is None
    assert _pctl_current([]) is None

def test_percentile_verdict_bands():
    assert percentile_verdict(0.20) == "undervalued"
    assert percentile_verdict(0.50) == "fair"
    assert percentile_verdict(0.85) == "overvalued"
    assert percentile_verdict(None) == "NA"


# ---------- PEG ----------

def test_peg_computation_and_guards():
    assert peg_of(20.0, 0.15) == pytest.approx(20 / 15)
    assert peg_of(-10.0, 0.15) is None       # 亏损
    assert peg_of(20.0, -0.05) is None       # 负增速
    assert peg_of(20.0, None) is None


def test_growth_verdict_bands():
    assert growth_verdict(0.8, None)[0] == "undervalued"
    assert growth_verdict(1.5, None)[0] == "fair"
    v, notes = growth_verdict(3.5, None)
    assert v == "overvalued" and any("透支" in n for n in notes)


def test_growth_verdict_falls_back_to_ps():
    v, notes = growth_verdict(None, 0.25)
    assert v == "undervalued"
    assert any("PS" in n for n in notes)


# ---------- 周期双验证 ----------

def test_cyclical_bottom_low_pb_is_undervalued():
    v, notes = cyclical_verdict(0.22, "bottom")
    assert v == "undervalued" and any("双重确认" in n for n in notes)


def test_cyclical_peak_high_pb_is_overvalued():
    v, notes = cyclical_verdict(0.85, "peak")
    assert v == "overvalued"


def test_cyclical_never_uses_pe():
    for pos in ("bottom", "peak", "recovery", "downturn", None):
        _, notes = cyclical_verdict(0.5, pos)
        assert any("禁用 PE" in n for n in notes)


def test_cyclical_downturn_low_pb_warns_catching():
    _, notes = cyclical_verdict(0.25, "downturn")
    assert any("左左侧" in n for n in notes)


def test_cyclical_missing_position_single_leg():
    v, notes = cyclical_verdict(0.25, None)
    assert v == "undervalued" and any("单腿" in n for n in notes)


# ---------- 红利利差 ----------

def test_dividend_spread_and_verdict():
    assert dividend_spread(4.0, 1.8) == pytest.approx(2.2)
    assert dividend_spread(None, 1.8) is None

    signal, desc = dividend_verdict(2.5, 0.75)
    assert signal == "dividend_attractive" and "类债" in desc

    signal, _ = dividend_verdict(0.5, 0.3)
    assert signal == "dividend_weak"

    signal, _ = dividend_verdict(1.5, 0.5)
    assert signal is None
