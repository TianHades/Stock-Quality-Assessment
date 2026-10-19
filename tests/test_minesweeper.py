"""排雷规则单元测试（离线，合成数据）。"""
from sqa.minesweeper.models import MsResult, MsVerdict, assess, combo_bonus, score_results
from sqa.minesweeper import rules_financial as fin
from sqa.minesweeper import rules_nonfinancial as nonfin


def _r(rule, layer, verdict):
    return MsResult(rule, layer, verdict, "")


# ---------- L0 审计 ----------

def test_rule_0_1_standard_opinion_passes():
    rows = [{"end_date": "20251231", "audit_result": "标准无保留意见"}]
    assert nonfin.rule_0_1(rows).verdict is MsVerdict.PASS


def test_rule_0_1_qualified_opinion_fails():
    rows = [{"end_date": "20251231", "audit_result": "保留意见"}]
    assert nonfin.rule_0_1(rows).verdict is MsVerdict.FAIL
    rows2 = [{"end_date": "20251231", "audit_result": "无法表示意见"}]
    assert nonfin.rule_0_1(rows2).verdict is MsVerdict.FAIL


def test_rule_0_1_emphasis_paragraph_still_passes():
    rows = [{"end_date": "20251231", "audit_result": "带强调事项段的无保留意见"}]
    assert nonfin.rule_0_1(rows).verdict is MsVerdict.PASS


def test_rule_0_2_late_disclosure_fails():
    late = [{"end_date": "20241231", "ann_date": "20250520"}]   # 截止 20250430
    assert nonfin.rule_0_2(late).verdict is MsVerdict.FAIL
    ok = [{"end_date": "20241231", "ann_date": "20250425"}]
    assert nonfin.rule_0_2(ok).verdict is MsVerdict.PASS


# ---------- L1 ----------

def test_rule_1_1_gm_swing_fails():
    assert fin.rule_1_1([25.0, 12.0]).verdict is MsVerdict.FAIL   # -13pp
    assert fin.rule_1_1([12.0, 18.0]).verdict is MsVerdict.WARN   # +6pp
    assert fin.rule_1_1([20.0, 22.0]).verdict is MsVerdict.PASS


def test_rule_1_2_three_conditions_fail():
    gm = [30.0, 32.0]          # 毛利率↑
    ar = [100.0, 150.0]        # 应收 +50%
    ap = [80.0, 60.0]          # 应付↓
    rev = [1000.0, 1100.0]     # 营收 +10% → 应收增速>营收
    assert fin.rule_1_2(gm, ar, ap, rev).verdict is MsVerdict.FAIL


# ---------- L2 ----------

def test_rule_2_3_high_cash_high_rate_debt_fails():
    balance = {"money_cap": 200.0, "st_borr": 100.0, "lt_borr": 50.0, "bond_payable": 0.0}
    assert fin.rule_2_3(balance, fin_exp=-12.0, base_rate=3.5).verdict is MsVerdict.FAIL
    # 现金少于有息负债一半 → PASS（利率正常）
    ok = {"money_cap": 30.0, "st_borr": 100.0, "lt_borr": 0.0, "bond_payable": 0.0}
    assert fin.rule_2_3(ok, fin_exp=-4.0, base_rate=3.5).verdict is MsVerdict.PASS


def test_rule_2_3_zero_or_immaterial_debt_is_pass_not_skip():
    # 无有息负债：存贷双高不适用 → PASS（此前会 SKIP）
    no_debt = {"money_cap": 1000.0, "total_assets": 5000.0}
    result = fin.rule_2_3(no_debt, fin_exp=None, base_rate=3.5)
    assert result.verdict is MsVerdict.PASS
    # 有息负债占总资产 <1%（茅台式）：财务费用净收入/极少借款不触发 FAIL
    tiny = {"money_cap": 5000.0, "non_cur_liab_due_1y": 4.0,
            "total_assets": 100000.0}
    result = fin.rule_2_3(tiny, fin_exp=-800.0, base_rate=3.5)
    assert result.verdict is MsVerdict.PASS


# ---------- L3 ----------

def test_rule_3_2_inventory_turn_down_gm_up_combo():
    # 周转 -25% + 毛利率 +1.5pp → WARN；+4pp → FAIL
    assert fin.rule_3_2([2.0, 1.5], [20.0, 21.5]).verdict is MsVerdict.WARN
    assert fin.rule_3_2([2.0, 1.5], [20.0, 24.0]).verdict is MsVerdict.FAIL


# ---------- L4 ----------

def test_rule_4_1_consecutive_below_one_fails():
    ocf = [80.0, 70.0, 60.0, 50.0, 40.0]
    np_ = [100.0, 100.0, 100.0, 100.0, 100.0]
    assert fin.rule_4_1(ocf, np_).verdict is MsVerdict.FAIL
    assert fin.rule_4_1([100.0] * 5, np_).verdict is MsVerdict.PASS


# ---------- L5 / S ----------

def test_rule_5_7_goodwill_fails_over_40():
    assert nonfin.rule_5_7({"goodwill": 50.0, "total_hldr_eqy_exc_min_int": 100.0}).verdict is MsVerdict.FAIL
    assert nonfin.rule_5_7({"goodwill": 25.0, "total_hldr_eqy_exc_min_int": 100.0}).verdict is MsVerdict.WARN
    assert nonfin.rule_5_7({"goodwill": 10.0, "total_hldr_eqy_exc_min_int": 100.0}).verdict is MsVerdict.PASS
    assert nonfin.rule_5_7({"goodwill": None, "total_hldr_eqy_exc_min_int": 100.0}).verdict is MsVerdict.PASS


def test_rule_s1_pledge_thresholds():
    assert nonfin.rule_s1(60.0).verdict is MsVerdict.FAIL
    assert nonfin.rule_s1(40.0).verdict is MsVerdict.WARN
    assert nonfin.rule_s1(10.0).verdict is MsVerdict.PASS


def test_rule_5_8_tiny_base_exemption():
    # 占比 0.3% 基数上 YoY 55% → 最多 WARN（双汇误报案例）
    def bal(oth, oth_prev):
        return [{"oth_receiv": oth_prev, "total_assets": 1000.0},
                {"oth_receiv": oth, "total_assets": 1000.0}]
    r = nonfin.rule_5_8(bal(3.1, 2.0), [1000.0, 1000.0])   # 0.31%，YoY 55%
    assert r.verdict is MsVerdict.WARN
    r2 = nonfin.rule_5_8(bal(8.0, 5.0), [1000.0, 1000.0])  # 0.8% 且 YoY 60%
    assert r2.verdict is MsVerdict.FAIL                     # 非小额则 FAIL 保留
    r3 = nonfin.rule_5_8(bal(60.0, 40.0), [1000.0, 1000.0])  # 6% 占比直接 FAIL
    assert r3.verdict is MsVerdict.FAIL


def test_rule_s2_short_term_coverage():
    def bal(cash, st):
        return {"money_cap": cash, "st_borr": st, "ncl_1y": 0.0, "total_assets": 1000.0}
    assert nonfin.rule_s2(bal(80.0, 100.0)).verdict is MsVerdict.FAIL    # 0.8
    assert nonfin.rule_s2(bal(120.0, 100.0)).verdict is MsVerdict.WARN   # 1.2
    assert nonfin.rule_s2(bal(200.0, 100.0)).verdict is MsVerdict.PASS   # 2.0


# ---------- 4.4 / 5.1（茅台实跑校准的回归用例） ----------

def test_rule_4_4_must_subtract_business_tax():
    # 真实字段口径：biz_tax_surchg（税金及附加）+ n_income（总净利，少数股东前）
    row = {"revenue": 1500.0, "oper_cost": 100.0, "biz_tax_surchg": 180.0,
           "sell_exp": 50.0, "admin_exp": 60.0, "fin_exp": -130.0,
           "n_income": 1000.0, "n_income_attr_p": 750.0}
    r = fin.rule_4_4(row)
    assert r.verdict in (MsVerdict.PASS, MsVerdict.WARN, MsVerdict.FAIL)
    # 缺税金字段 → SKIP（宁缺毋滥）
    no_tax = dict(row); no_tax.pop("biz_tax_surchg")
    assert fin.rule_4_4(no_tax).verdict is MsVerdict.SKIP


def test_rule_5_1_agency_name_normalization():
    rows = [
        {"audit_agency": "立信"},
        {"audit_agency": "立信会计师事务所（特殊普通合伙）"},
        {"audit_agency": "立信会计师事务所(特殊普通合伙)"},
        {"audit_agency": "立信"},
    ]
    assert nonfin.rule_5_1(rows).verdict is MsVerdict.PASS  # 同一所不同写法


# ---------- 计分与闸门 ----------

def test_score_layer_weights():
    results = [_r("1.1", "L1", MsVerdict.WARN), _r("4.1", "L4", MsVerdict.FAIL)]
    assert score_results(results) == 2 + 7


def test_combo_bonus_rules():
    results = [_r("3.2", "L3", MsVerdict.FAIL)]
    assert combo_bonus(results) == (10, ["存货周转↓+毛利率↑ +10"])
    results = [_r("2.3", "L2", MsVerdict.FAIL), _r("4.1", "L4", MsVerdict.FAIL)]
    bonus, _ = combo_bonus(results)
    assert bonus == 8


def test_assess_gates():
    # 低风险：几条 WARN
    low = [_r("1.1", "L1", MsVerdict.WARN)] * 2        # 4 分
    assert assess(low)["verdict"] == "pass"

    # 中风险：12 分
    mid = [_r("2.2", "L2", MsVerdict.WARN)] * 4
    assert assess(mid)["verdict"] == "degraded"

    # 高风险：30 分 → blocked，且允许 --force
    high = [_r("2.2", "L2", MsVerdict.WARN)] * 10
    high_out = assess(high)
    assert high_out["verdict"] == "blocked" and high_out["forceable"] is True

    # 极高风险：50 分 → blocked 但不可 --force
    extreme = [_r("4.1", "L4", MsVerdict.FAIL)] * 8   # 56 分
    extreme_out = assess(extreme)
    assert extreme_out["risk_level"] == "extreme"
    assert extreme_out["verdict"] == "blocked" and extreme_out["forceable"] is False

    # L0 FAIL 直接排除（不可 force）
    excluded = [_r("0.1", "L0", MsVerdict.FAIL), _r("1.1", "L1", MsVerdict.PASS)]
    out = assess(excluded)
    assert out["verdict"] == "excluded" and out["risk_level"] == "excluded"
    assert out["forceable"] is False


def test_s4_is_combo_only_not_layer_scored():
    # doc 02 §3：S4 只计组合加分 +5，不另计 L5 层分 3
    out = assess([_r("S4", "S", MsVerdict.FAIL)])
    assert out["base_score"] == 0
    assert out["combo_bonus"] == 5
    assert out["total_score"] == 5


def test_rule_3_3_requires_consecutive_years():
    # 在建工程高增/回落交替：不应累计成"连续 3 年"
    cip = [100.0, 140.0, 100.0, 140.0, 100.0, 140.0]
    fix = [1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1050.0]
    result = fin.rule_3_3(cip, fix)
    assert result.verdict is MsVerdict.WARN  # 最长连续 1 年，不是 FAIL


def test_rule_4_3_negative_revenue_not_fail():
    # 营收负增长时 a_g > r_g*3 恒真，修复后不应误报
    result = fin.rule_4_3([100.0, 110.0, 120.0],
                          [100.0, 90.0, 80.0], [10.0, 11.0, 12.0])
    assert result.verdict is MsVerdict.PASS
