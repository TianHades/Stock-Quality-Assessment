"""周期性判定（01 号文档 §5.1 R1/R2）的单元测试。"""
from sqa.classify.cycle import is_cycle_industry, is_cycle_quantitative
from sqa.config import Thresholds

CFG = Thresholds()


# ---------- R1 行业清单 ----------

def test_r1_industry_keywords_hit():
    assert is_cycle_industry("生猪养殖") is True
    assert is_cycle_industry("普钢") is True
    assert is_cycle_industry("煤炭开采") is True
    assert is_cycle_industry("化学原料") is True
    assert is_cycle_industry("水泥") is True
    assert is_cycle_industry("航运") is True
    assert is_cycle_industry("工程机械") is True
    assert is_cycle_industry("光伏设备") is False  # 光伏是成长/制造，不在周期清单
    # 2026-10 tushare 行业串校准
    assert is_cycle_industry("水运") is True
    assert is_cycle_industry("渔业") is True
    assert is_cycle_industry("塑料") is True
    assert is_cycle_industry("橡胶") is True
    assert is_cycle_industry("日用化工") is False  # 化妆品消费，含"化工"但白名单排除


def test_r1_industry_miss_for_consumer_tech():
    assert is_cycle_industry("白酒") is False
    assert is_cycle_industry("白色家电") is False
    assert is_cycle_industry("半导体") is False
    assert is_cycle_industry("软件开发商") is False


def test_r1_empty_industry_is_miss():
    assert is_cycle_industry("") is False


# ---------- R2 量化特征 ----------

def _series(values):
    return list(values)


def test_r2_hit_when_margin_swing_and_profit_swing():
    # 毛利率极差 25pp，净利润峰谷比 8 —— 典型周期特征
    margins = _series([28.0, 25.0, 15.0, 12.0, 18.0, 22.0, 26.0, 20.0])
    profits = _series([100.0, 60.0, 8.0, 2.0, 30.0, 70.0, 95.0, 50.0])
    result = is_cycle_quantitative(margins, profits, CFG)
    assert result.verdict.value == "HIT"


def test_r2_miss_when_margin_stable_even_with_loss():
    margins = _series([10.0, 12.0, 11.5, 13.0, 12.0, 11.0, 12.5, 11.8, 12.2, 11.5])
    profits = _series([50.0, 40.0, -20.0, 30.0, 45.0, 55.0, 48.0, 52.0, 47.0, 51.0])
    # 毛利率极差 3pp 不满足 —— 两条件是 AND，即使经历亏损也为 MISS
    result = is_cycle_quantitative(margins, profits, CFG)
    assert result.verdict.value == "MISS"


def test_r2_miss_for_monotonic_growth():
    # 回归：宁德式"从低基数长大"——毛利率中枢下移 23pp + 利润单调大增，
    # 峰谷比口径会误判周期，最大回撤口径必须 MISS
    margins = _series([38.0, 36.0, 34.0, 27.0, 26.0, 17.0, 22.0, 24.0, 23.0, 24.0])
    profits = _series([10.0, 30.0, 33.0, 45.0, 159.0, 307.0, 300.0, 441.0, 507.0, 590.0])
    result = is_cycle_quantitative(margins, profits, CFG)
    assert result.verdict.value == "MISS"


def test_r2_miss_for_stable_business():
    margins = _series([40.0, 41.0, 39.5, 40.5, 40.2, 41.2, 39.8, 40.6])
    profits = _series([100.0, 110.0, 105.0, 115.0, 112.0, 120.0, 118.0, 125.0])
    result = is_cycle_quantitative(margins, profits, CFG)
    assert result.verdict.value == "MISS"


def test_r2_skip_when_insufficient_history():
    margins = _series([30.0, 10.0, 28.0])
    profits = _series([100.0, 5.0, 90.0])
    result = is_cycle_quantitative(margins, profits, CFG)
    assert result.verdict.value == "SKIP"
