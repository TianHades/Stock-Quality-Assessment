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

def test_rule_1_6_materiality_calibrated():
    """D 步：减值 YoY 必须叠加利润占比材料性，避免小基数噪声 FAIL。"""
    def inc(cur, prev, np_):
        return [{"assets_impair_loss": prev, "credit_impa_loss": 0.0,
                 "n_income_attr_p": np_},
                {"assets_impair_loss": cur, "credit_impa_loss": 0.0,
                 "n_income_attr_p": np_}]

    # 0.3% 利润占比、YoY 200% → 噪声 PASS（旧规则会 FAIL）
    assert fin.rule_1_6(inc(0.3, 0.1, 100.0)).verdict is MsVerdict.PASS
    # 6% 利润占比、YoY 200% → 有实质影响 FAIL
    assert fin.rule_1_6(inc(6.0, 2.0, 100.0)).verdict is MsVerdict.FAIL
    # 6% 利润占比但 YoY 20% → WARN（占比高但未暴增）
    assert fin.rule_1_6(inc(6.0, 5.0, 100.0)).verdict is MsVerdict.WARN
    # 11% 利润占比、YoY 10% → FAIL（占比阈值 10%）
    assert fin.rule_1_6(inc(11.0, 10.0, 100.0)).verdict is MsVerdict.FAIL


def test_rule_2_3_high_cash_high_rate_debt_fails():
    balance = {"money_cap": 200.0, "st_borr": 100.0, "lt_borr": 50.0, "bond_payable": 0.0}
    assert fin.rule_2_3(balance, interest_exp=12.0, base_rate=3.5).verdict is MsVerdict.FAIL
    # 现金少于有息负债一半 → PASS（利率正常）
    ok = {"money_cap": 30.0, "st_borr": 100.0, "lt_borr": 0.0, "bond_payable": 0.0}
    assert fin.rule_2_3(ok, interest_exp=4.0, base_rate=3.5).verdict is MsVerdict.PASS


def test_rule_2_3_uses_interest_exp_not_net_finance():
    # 美的式：净财务费用为负（利息收入），但利息支出很小 → PASS
    balance = {"money_cap": 200.0, "st_borr": 100.0, "total_assets": 1000.0}
    assert fin.rule_2_3(balance, interest_exp=2.0, base_rate=3.5).verdict is MsVerdict.PASS


def test_rule_2_3_zero_or_immaterial_debt_is_pass_not_skip():
    # 无有息负债：存贷双高不适用 → PASS（此前会 SKIP）
    no_debt = {"money_cap": 1000.0, "total_assets": 5000.0}
    result = fin.rule_2_3(no_debt, interest_exp=None, base_rate=3.5)
    assert result.verdict is MsVerdict.PASS
    # 有息负债占总资产 <1%（茅台式）：财务费用净收入/极少借款不触发 FAIL
    tiny = {"money_cap": 5000.0, "non_cur_liab_due_1y": 4.0,
            "total_assets": 100000.0}
    result = fin.rule_2_3(tiny, interest_exp=8.0, base_rate=3.5)
    assert result.verdict is MsVerdict.PASS


# ---------- L3 ----------

def test_rule_3_2_inventory_turn_down_gm_up_combo():
    # 周转 -25% + 毛利率 +1.5pp → WARN；+4pp → FAIL
    assert fin.rule_3_2([2.0, 1.5], [20.0, 21.5]).verdict is MsVerdict.WARN
    assert fin.rule_3_2([2.0, 1.5], [20.0, 24.0]).verdict is MsVerdict.FAIL


# ---------- L2 资本开支 ----------

def test_rule_2_1_uses_capex_not_total_investment_cf():
    # 2026-10-25 复核修正：理财型投资现金流不得计入"扩张烧钱"
    ocf = [10.0] * 6
    capex = [9.0, 9.0, 9.0, 9.0, 2.0, 2.0]
    assert fin.rule_2_1(ocf, capex).verdict is MsVerdict.FAIL   # 4 年命中
    assert fin.rule_2_1([10.0] * 4, [9.0, 9.0, 2.0, 2.0]).verdict is MsVerdict.WARN
    assert fin.rule_2_1([10.0] * 4, [2.0] * 4).verdict is MsVerdict.PASS
    # capex 全缺 / 有效配对 <3 年 → SKIP（不猜测）
    assert fin.rule_2_1([10.0] * 4, [None] * 4).verdict is MsVerdict.SKIP
    assert fin.rule_2_1([10.0] * 4, [9.0]).verdict is MsVerdict.SKIP


# ---------- L4 ----------

def test_rule_4_1_consecutive_below_one_fails():
    ocf = [80.0, 70.0, 60.0, 50.0, 40.0]
    np_ = [100.0, 100.0, 100.0, 100.0, 100.0]
    assert fin.rule_4_1(ocf, np_).verdict is MsVerdict.FAIL
    assert fin.rule_4_1([100.0] * 5, np_).verdict is MsVerdict.PASS


def test_rule_4_1_uses_recent_5_year_window():
    # 历史 6 年 <1，但近 5 年只有 3 年 <1（最长连续 2 年）→ WARN 而非 FAIL
    ocf = [50.0] * 6 + [160.0, 99.0, 79.0, 165.0]
    np_ = [100.0] * 10
    result = fin.rule_4_1(ocf, np_)
    assert result.verdict is MsVerdict.WARN
    assert "历史最长连续" in result.detail
    # 近 5 年连续 3 年 <1 → FAIL
    recent_fail = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
                   100.0, 50.0, 50.0, 50.0]
    assert fin.rule_4_1(recent_fail, np_).verdict is MsVerdict.FAIL


def test_rule_4_1_window_is_calendar_years_not_valid_points():
    # 最近 5 个年度中 4 年亏损：亏损年按"CF/净利<1"命中计（不得 SKIP/PASS）
    ocf = [200.0] * 6 + [50.0, 50.0, 50.0, 50.0]
    np_ = [100.0] * 6 + [-10.0, -10.0, -10.0, -10.0]
    assert fin.rule_4_1(ocf, np_).verdict is MsVerdict.FAIL


def test_rule_4_1_loss_years_count_as_hits():
    # 近 5 年 3 年命中（含 1 年亏损、2 年低比值）但无连续 3 年 → WARN
    ocf = [150.0, 150.0, 150.0, 150.0, 150.0,
           150.0, 50.0, 150.0, 50.0, 50.0]
    np_ = [100.0] * 10
    np_[6] = -10.0  # 亏损年（负比值 <1）
    result = fin.rule_4_1(ocf, np_)
    assert result.verdict is MsVerdict.WARN
    # 最近 5 年数据全缺失 → SKIP（不得 PASS）
    assert fin.rule_4_1([50.0] * 5, [None] * 5).verdict is MsVerdict.SKIP


def test_rule_4_5_uses_recent_5_year_window():
    np_ = [float(i) for i in range(1, 11)]  # 逐年增长
    # 历史 4 年命中 + 近 5 年 2 年命中 → WARN（旧口径 6 年 → FAIL）
    fcf = [-1.0, -1.0, -1.0, -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0]
    result = fin.rule_4_5(np_, fcf)
    assert result.verdict is MsVerdict.WARN
    assert "历史" in result.detail
    # 近 5 年 3 年命中 → FAIL
    fcf_fail = [-1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 1.0, 1.0]
    assert fin.rule_4_5(np_, fcf_fail).verdict is MsVerdict.FAIL
    # 近 5 年 0-1 年命中 → PASS
    assert fin.rule_4_5(np_, [1.0] * 10).verdict is MsVerdict.PASS
    # FCF 全缺失 → SKIP
    assert fin.rule_4_5(np_, [None] * 10).verdict is MsVerdict.SKIP
    # 最近 5 个年度数据全缺失 → SKIP（不得回退到更早命中）
    assert fin.rule_4_5(np_, [-1.0] * 5 + [None] * 5).verdict is MsVerdict.SKIP
    # 近 5 年有效数据 <3 → SKIP（不得 PASS）
    assert fin.rule_4_5([1.0, 2.0, 3.0, 4.0, 5.0],
                        [None] * 4 + [-1.0]).verdict is MsVerdict.SKIP
    # np_/fcf 长度不一致：按公共年份对齐，不越界/错位
    misaligned = fin.rule_4_5([float(i) for i in range(1, 11)],
                              [-1.0] * 5 + [None] * 4 + [-1.0] + [-1.0])
    assert misaligned.verdict is MsVerdict.SKIP


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
    # 占比 0.3% / 0.8% 基数上 YoY 高增 → 最多 WARN（C 步把 FAIL 门槛提到 1%）
    def bal(oth, oth_prev):
        return [{"oth_receiv": oth_prev, "total_assets": 1000.0},
                {"oth_receiv": oth, "total_assets": 1000.0}]
    r = nonfin.rule_5_8(bal(3.1, 2.0), [1000.0, 1000.0])   # 0.31%，YoY 55%
    assert r.verdict is MsVerdict.WARN
    r2 = nonfin.rule_5_8(bal(8.0, 5.0), [1000.0, 1000.0])  # 0.8% 且 YoY 60%
    assert r2.verdict is MsVerdict.WARN                     # <1% 仍不 FAIL
    r2b = nonfin.rule_5_8(bal(12.0, 7.0), [1000.0, 1000.0])  # 1.2% 且 YoY 71%
    assert r2b.verdict is MsVerdict.FAIL                     # ≥1% 且高增 → FAIL
    r3 = nonfin.rule_5_8(bal(60.0, 40.0), [1000.0, 1000.0])  # 6% 占比直接 FAIL
    assert r3.verdict is MsVerdict.FAIL


def test_rule_s2_short_term_coverage():
    def bal(cash, st):
        return {"money_cap": cash, "st_borr": st, "ncl_1y": 0.0, "total_assets": 1000.0}
    assert nonfin.rule_s2(bal(80.0, 100.0)).verdict is MsVerdict.FAIL    # 0.8
    assert nonfin.rule_s2(bal(120.0, 100.0)).verdict is MsVerdict.WARN   # 1.2
    assert nonfin.rule_s2(bal(200.0, 100.0)).verdict is MsVerdict.PASS   # 2.0


# ---------- 4.4 / 5.1（茅台实跑校准的回归用例） ----------

def test_rule_4_4_must_subtract_business_tax_and_rd():
    # 真实字段口径：biz_tax_surchg（税金及附加）+ rd_exp（研发费用）
    # 2026-10-25 复核：比较基准改为营业利润（税前口径），避免所得税造成的假背离
    base = {"revenue": 1500.0, "oper_cost": 100.0, "biz_tax_surchg": 180.0,
            "sell_exp": 50.0, "admin_exp": 60.0, "fin_exp": -130.0,
            "rd_exp": 0.0, "operate_profit": 1000.0}
    # rd=0 时 core=1240 → 与营业利润背离 24% → WARN
    assert fin.rule_4_4(dict(base)).verdict is MsVerdict.WARN
    # 扣减研发费用 240 后 core=1000 → 背离 0% → PASS（2026-10-25 复核修正）
    with_rd = dict(base, rd_exp=240.0)
    assert fin.rule_4_4(with_rd).verdict is MsVerdict.PASS
    # 营业利润缺失回退利润总额（同为税前口径）；二者都缺失 → SKIP
    total = dict(base)
    total.pop("operate_profit")
    total["total_profit"] = 1000.0
    assert fin.rule_4_4(total).verdict is MsVerdict.WARN
    no_basis = dict(base)
    no_basis.pop("operate_profit")
    assert fin.rule_4_4(no_basis).verdict is MsVerdict.SKIP
    # 缺税金字段 / 缺研发费用字段 → SKIP（宁缺毋滥）
    no_tax = dict(base)
    no_tax.pop("biz_tax_surchg")
    assert fin.rule_4_4(no_tax).verdict is MsVerdict.SKIP
    no_rd = dict(base)
    no_rd.pop("rd_exp")
    assert fin.rule_4_4(no_rd).verdict is MsVerdict.SKIP


def test_rule_5_1_agency_name_normalization():
    rows = [
        {"audit_agency": "立信"},
        {"audit_agency": "立信会计师事务所（特殊普通合伙）"},
        {"audit_agency": "立信会计师事务所(特殊普通合伙)"},
        {"audit_agency": "立信"},
    ]
    assert nonfin.rule_5_1(rows).verdict is MsVerdict.PASS  # 同一所不同写法

    # D 步：有限公司/地区括号变体也不得误计为换所
    variants = [
        {"audit_agency": "德勤华永"},
        {"audit_agency": "德勤华永事务所有限公司"},
        {"audit_agency": "四川华信(集团)"},
        {"audit_agency": "四川华信(集团)会计师事务有限责任公司"},
        {"audit_agency": "四川华信(集团)事务所有限责任公司"},
    ]
    by_name = {"德勤华永": variants[:2], "四川华信(集团)": variants[2:]}
    for name, rows in by_name.items():
        assert nonfin.rule_5_1(rows).verdict is MsVerdict.PASS


def test_rule_5_1_uses_recent_5_year_window():
    # 2016-2018 A、2019-2023 B、2024-2025 C：
    # 最近 5 年（2021-2025）只看到 B→C 一次 → WARN，而不是 10 年窗口的 FAIL
    rows = []
    for year in range(2016, 2026):
        agency = "A所" if year <= 2018 else ("B所" if year <= 2023 else "C所")
        rows.append({"end_date": f"{year}1231", "audit_agency": agency})
    assert nonfin.rule_5_1(rows).verdict is MsVerdict.WARN
    # 最近 5 年 A→B→A 两次切换 → FAIL
    recent = [{"end_date": "20211231", "audit_agency": "A所"},
              {"end_date": "20221231", "audit_agency": "A所"},
              {"end_date": "20231231", "audit_agency": "B所"},
              {"end_date": "20241231", "audit_agency": "A所"},
              {"end_date": "20251231", "audit_agency": "A所"}]
    assert nonfin.rule_5_1(recent).verdict is MsVerdict.FAIL


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
    ta = [10000.0] * 6
    result = fin.rule_3_3(cip, fix, ta)
    assert result.verdict is MsVerdict.WARN  # 最长连续 1 年，不是 FAIL


def test_rule_3_3_materiality_floor():
    # 2026-10-25 复核修正：在建工程/总资产 <1% 的年份不计入（低基数不误报）
    cip = [0.04, 0.10, 0.20]
    fix = [10.0, 10.1, 10.2]
    ta = [100.0, 100.0, 100.0]
    assert fin.rule_3_3(cip, fix, ta).verdict is MsVerdict.PASS
    # 材料性达标（≥1%）且连续高增：仍正常触发
    cip2 = [2.0, 4.0, 8.0]
    ta2 = [100.0, 100.0, 100.0]
    assert fin.rule_3_3(cip2, fix, ta2).verdict is MsVerdict.WARN
    # total_assets 全缺失/有效不足 3 年：不猜测，SKIP
    assert fin.rule_3_3(cip2, fix, [None] * 3).verdict is MsVerdict.SKIP
    assert fin.rule_3_3(cip2, fix, [100.0]).verdict is MsVerdict.SKIP


def test_rule_4_3_negative_revenue_not_fail():
    # 营收负增长时 a_g > r_g*3 恒真，修复后不应误报
    result = fin.rule_4_3([100.0, 110.0, 120.0],
                          [100.0, 90.0, 80.0], [10.0, 11.0, 12.0])
    assert result.verdict is MsVerdict.PASS


# ---------- 2026-10-25 复核修正：5.2 明细口径（名义持有人/稀释/减持） ----------

def _holder_period(end_date, holders):
    return {"end_date": end_date, "holders": [
        {"name": n, "ratio": r, "amount": a, "nominee": nom}
        for n, r, a, nom in holders]}


def test_rule_5_2_excludes_nominee_holder():
    # 比亚迪式：HKSCC 名义账户比例上升，但实际控制人持股数量未变 → PASS
    periods = [
        _holder_period("20221231", [("HKSCCNOMINEESLIMITED", 37.70, 999, True),
                                    ("王传福", 17.64, 513, False)]),
        _holder_period("20251231", [("HKSCCNOMINEESLIMITED", 40.38, 1200, True),
                                    ("王传福", 16.90, 513, False)]),
    ]
    result = nonfin.rule_5_2(periods)
    assert result.verdict is MsVerdict.PASS
    assert "王传福" in result.detail and "持股数量" in result.detail


def test_rule_5_2_dilution_passes_and_real_sell_fails():
    # 比例下降但股数未变 = 股本扩张稀释 → PASS
    diluted = [
        _holder_period("20221231", [("甲", 52.0, 1000, False)]),
        _holder_period("20251231", [("甲", 43.0, 1000, False)]),
    ]
    assert nonfin.rule_5_2(diluted).verdict is MsVerdict.PASS
    # 股数连续减少且累计 >5% → FAIL
    sold = [
        _holder_period("20221231", [("甲", 52.0, 1000, False)]),
        _holder_period("20231231", [("甲", 49.0, 960, False)]),
        _holder_period("20241231", [("甲", 46.0, 930, False)]),
        _holder_period("20251231", [("甲", 43.0, 900, False)]),
    ]
    assert nonfin.rule_5_2(sold).verdict is MsVerdict.FAIL
    # 连续小幅减少（累计 2%）→ WARN
    small = [
        _holder_period("20221231", [("甲", 52.0, 1000, False)]),
        _holder_period("20231231", [("甲", 51.0, 995, False)]),
        _holder_period("20241231", [("甲", 50.5, 985, False)]),
        _holder_period("20251231", [("甲", 50.0, 980, False)]),
    ]
    assert nonfin.rule_5_2(small).verdict is MsVerdict.WARN
    # 小幅非连续减少 → PASS（未构成趋势性减持；>5% 的划转/EB 由深度模式披露豁免）
    lumpy = [
        _holder_period("20221231", [("甲", 52.25, 1000, False)]),
        _holder_period("20231231", [("甲", 49.13, 1040, False)]),
        _holder_period("20241231", [("甲", 46.81, 985, False)]),
        _holder_period("20251231", [("甲", 43.47, 970, False)]),
    ]
    assert nonfin.rule_5_2(lumpy).verdict is MsVerdict.PASS


def test_rule_5_2_accepts_dict_series_without_holders():
    # 防御：非 fetch_holder_series 结构的 dict 列表按 ratio 字段降级，不崩溃
    series = [{"ratio": 52.0}, {"ratio": 51.0}, {"ratio": 50.0}]
    result = nonfin.rule_5_2(series)
    assert result.verdict in (MsVerdict.PASS, MsVerdict.WARN, MsVerdict.FAIL,
                              MsVerdict.SKIP)
