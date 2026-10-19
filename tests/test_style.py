"""成长/价值/均衡判定（01 号文档 §5.3 G1/V1/E1）的单元测试。"""
from sqa.classify.style import decide_style, rule_g1, rule_v1
from sqa.config import Thresholds

CFG = Thresholds()


def test_g1_hit_by_revenue_cagr():
    r = rule_g1(rev_cagr=0.18, np_dedt_cagr=0.10, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_g1_hit_by_np_cagr():
    r = rule_g1(rev_cagr=0.10, np_dedt_cagr=0.25, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_g1_miss_when_both_below():
    r = rule_g1(rev_cagr=0.12, np_dedt_cagr=0.15, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_g1_skip_when_data_missing():
    r = rule_g1(rev_cagr=None, np_dedt_cagr=None, cfg=CFG)
    assert r.verdict.value == "SKIP"


def test_v1_hit_low_valuation_and_slow_growth():
    r = rule_v1(rev_cagr=0.05, pe_pctl=0.20, pb_pctl=0.45, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_v1_hit_by_pb_percentile():
    r = rule_v1(rev_cagr=0.05, pe_pctl=0.50, pb_pctl=0.15, cfg=CFG)
    assert r.verdict.value == "HIT"


def test_v1_miss_when_valuation_not_cheap():
    r = rule_v1(rev_cagr=0.05, pe_pctl=0.55, pb_pctl=0.60, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_v1_miss_when_still_growing():
    # 增速 12% 超过价值类上限，即使便宜也不算价值（成长优先于价值已在上层处理）
    r = rule_v1(rev_cagr=0.12, pe_pctl=0.20, pb_pctl=0.20, cfg=CFG)
    assert r.verdict.value == "MISS"


def test_decide_style_growth_priority():
    # 高增速 + 低估值 → 成长，同时留下 low_valuation_percentile 信号
    style, signals = decide_style(rev_cagr=0.25, np_dedt_cagr=0.30,
                                  pe_pctl=0.15, pb_pctl=0.20, cfg=CFG)
    assert style == "growth"
    assert "low_valuation_percentile" in signals


def test_decide_style_value():
    style, signals = decide_style(rev_cagr=0.05, np_dedt_cagr=0.06,
                                  pe_pctl=0.20, pb_pctl=0.35, cfg=CFG)
    assert style == "value"
    assert signals == ()


def test_decide_style_balanced_fallback():
    style, _ = decide_style(rev_cagr=0.12, np_dedt_cagr=0.15,
                            pe_pctl=0.50, pb_pctl=0.55, cfg=CFG)
    assert style == "balanced"


def test_decide_style_insufficient_data_defaults_by_growth_none():
    # 增速数据缺失（上市不足3年等）→ 无法判成长，估值中性 → 均衡兜底
    style, _ = decide_style(rev_cagr=None, np_dedt_cagr=None,
                            pe_pctl=0.50, pb_pctl=0.55, cfg=CFG)
    assert style == "balanced"
