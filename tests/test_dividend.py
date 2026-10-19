"""红利属性判定（01 号文档 §5.2 D1-D4）的单元测试。"""
from sqa.classify.dividend import rule_d1, rule_d2, rule_d3, rule_d4
from sqa.config import Thresholds

CFG = Thresholds()


def test_d1_hit_above_dynamic_floor():
    # rf=2% → 动态阈值 max(3%, 3.5%) = 3.5%；均值 3.8% 命中
    r = rule_d1([4.0, 3.6, 3.8], risk_free=0.02, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_d1_miss_below_dynamic_floor():
    # 均值 3.3% < 3.5%（动态）—— 高于静态3%但仍不命中，验证动态阈值生效
    r = rule_d1([3.5, 3.2, 3.2], risk_free=0.02, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_d1_miss_below_static_floor():
    r = rule_d1([2.0, 2.5, 1.8], risk_free=0.01, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_d1_skip_when_no_data():
    r = rule_d1([], risk_free=0.02, cfg=CFG)
    assert r.verdict.value == "SKIP"


def test_d2_hit_with_5yr_streak():
    assert rule_d2(5, CFG).verdict.value == "HIT"
    assert rule_d2(8, CFG).verdict.value == "HIT"


def test_d2_miss_with_short_streak():
    assert rule_d2(4, CFG).verdict.value == "MISS"
    assert rule_d2(0, CFG).verdict.value == "MISS"


def test_d3_hit_in_healthy_range():
    assert rule_d3(0.50, CFG).verdict.value == "HIT"
    assert rule_d3(0.30, CFG).verdict.value == "HIT"


def test_d3_hit_with_warning_near_ceiling():
    r = rule_d3(0.95, CFG)
    assert r.verdict.value == "HIT"
    assert "派息率" in r.detail  # 警示写进 detail


def test_d3_miss_outside_range():
    assert rule_d3(0.15, CFG).verdict.value == "MISS"
    assert rule_d3(1.20, CFG).verdict.value == "MISS"


def test_d3_skip_when_missing():
    assert rule_d3(None, CFG).verdict.value == "SKIP"


def test_d4_hit_when_fcf_covers():
    r = rule_d4(cum_div=80.0, cum_fcf=100.0, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_d4_miss_when_overdrawn():
    r = rule_d4(cum_div=120.0, cum_fcf=100.0, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_d4_miss_when_fcf_negative():
    r = rule_d4(cum_div=10.0, cum_fcf=-50.0, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_d4_skip_when_missing():
    r = rule_d4(cum_div=None, cum_fcf=None, cfg=CFG)
    assert r.verdict.value == "SKIP"
