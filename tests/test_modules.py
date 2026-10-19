"""专属模块单元测试（离线，纯函数部分）。"""
import pytest

from sqa.modules.common import pctl_latest, std, yoy_latest
from sqa.modules.cycle_position import capex_reading, classify_position
from sqa.modules.dividend_sustainability import verdict_of
from sqa.modules.growth_quality import verdict_of as growth_verdict
from sqa.modules.growth_quality import volatility_score
from sqa.modules.moat_check import erosion_warning


# ---------- 周期位置 ----------

def test_position_bottom_and_peak():
    assert classify_position(0.20, -0.3) == "bottom"
    assert classify_position(0.85, 0.2) == "peak"


def test_position_middle_by_direction():
    assert classify_position(0.50, 0.15) == "recovery"
    assert classify_position(0.50, -0.10) == "downturn"


def test_position_insufficient_data():
    assert classify_position(None, None) is None


def test_capex_reading_boundaries():
    assert "底部" in capex_reading(1.2)
    assert "激增" in capex_reading(3.0)
    assert "中性" in capex_reading(2.0)
    assert "SKIP" in capex_reading(None)


# ---------- 分红持续性 ----------

def test_dividend_at_risk_low_coverage():
    verdict, warns = verdict_of(latest_coverage=0.6, latest_payout=0.7,
                                payout_peak=0.8, streak=10)
    assert verdict == "at_risk" and any("覆盖" in w for w in warns)


def test_dividend_at_risk_payout_over_100():
    verdict, _ = verdict_of(latest_coverage=2.0, latest_payout=1.2,
                            payout_peak=1.2, streak=8)
    assert verdict == "at_risk"


def test_dividend_watch_thin_coverage():
    verdict, warns = verdict_of(latest_coverage=1.1, latest_payout=0.5,
                                payout_peak=0.6, streak=10)
    assert verdict == "watch" and any("偏薄" in w for w in warns)


def test_dividend_sustainable():
    verdict, warns = verdict_of(latest_coverage=2.0, latest_payout=0.6,
                                payout_peak=0.7, streak=10)
    assert verdict == "sustainable" and warns == []


def test_dividend_short_streak_is_watch():
    verdict, _ = verdict_of(latest_coverage=2.0, latest_payout=0.6,
                            payout_peak=0.7, streak=3)
    assert verdict == "watch"


def test_dividend_weakening_direction_and_payout_band():
    # 覆盖率改善（最新 1.3 > 上年 1.1）不得报"走弱"；恶化才报
    ok, warns = verdict_of(latest_coverage=1.3, latest_payout=0.6,
                           payout_peak=0.6, streak=10,
                           coverage_weakening=False)
    assert ok == "sustainable" and warns == []
    weakening, warns = verdict_of(latest_coverage=1.3, latest_payout=0.6,
                                  payout_peak=0.6, streak=10,
                                  coverage_weakening=True)
    assert weakening == "watch" and any("走弱" in w for w in warns)
    low_payout, warns = verdict_of(latest_coverage=2.0, latest_payout=0.05,
                                   payout_peak=0.6, streak=10)
    assert low_payout == "watch" and any("派息率" in w for w in warns)
    missing, _ = verdict_of(latest_coverage=None, latest_payout=None,
                            payout_peak=None, streak=10)
    assert missing == "watch"


# ---------- 成长质量 ----------

def test_volatility_score_anchors():
    assert volatility_score(0.30) == 0.0
    assert volatility_score(0.15) == 50.0
    assert volatility_score(0.05) == 100.0
    assert volatility_score(None) is None


def test_growth_verdict_bands():
    assert growth_verdict(80) == "solid"
    assert growth_verdict(60) == "acceptable"
    assert growth_verdict(30) == "poor"
    assert growth_verdict(None) == "insufficient_data"


# ---------- 护城河 ----------

def test_moat_erosion_warning():
    w = erosion_warning(gm_trend_pp=-8.0, gm_vs_peer_pp=-3.0)
    assert w is not None and "侵蚀" in w
    assert erosion_warning(-8.0, 5.0) is None    # 仍领先同行
    assert erosion_warning(2.0, -3.0) is None    # 趋势未恶化
    assert erosion_warning(None, None) is None


# ---------- 公共辅助 ----------

def test_pctl_latest():
    assert pctl_latest([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert pctl_latest([5, 4, 3, 2, 1]) == pytest.approx(0.2)
    assert pctl_latest([1, 2, None, 3]) is None  # 不足5点


def test_yoy_latest_guards():
    assert yoy_latest([100, 120]) == pytest.approx(0.2)
    assert yoy_latest([120, None]) is None
    assert yoy_latest([None, None]) is None


def test_std():
    assert std([1.0, 1.0, 1.0]) == 0.0
    assert std([1.0, 2.0]) is None               # 不足3点
    assert std([1.0, 2.0, 3.0]) == pytest.approx(1.0)
