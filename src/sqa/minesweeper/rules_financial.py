"""排雷规则 L1-L4：利润表 / 现金流 / 资产负债表 / 交叉验证（财务类，快筛可跑）。

阈值与判定逻辑复用上游 checklist-rules.md（02 号文档 §2），未做改动；
仅去掉依赖同行数据的分支（无同行管道时降级，明细注明）。
"""
from __future__ import annotations

from sqa.minesweeper.models import MsResult, MsVerdict


def _yoy(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return new / old - 1.0


def _f(row: dict, *names: str) -> float | None:
    """多候选字段名取第一个非空值（tushare 字段名版本差异防御）。"""
    for n in names:
        v = row.get(n)
        if v is not None:
            return float(v)
    return None


# ---------- Layer 1: 利润表信号 ----------

def rule_1_1(gm_series: list[float | None]) -> MsResult:
    """毛利率异常高 + 大幅波动（无同行数据时仅用 YoY 波动，上游降级条款）。"""
    vals = [g for g in gm_series if g is not None]
    if len(vals) < 2:
        return MsResult("1.1", "L1", MsVerdict.SKIP, "毛利率数据不足")
    gm_cur, gm_prev = vals[-1], vals[-2]
    change = gm_cur - gm_prev
    if abs(change) > 10:
        return MsResult("1.1", "L1", MsVerdict.FAIL,
                        f"毛利率 YoY 波动 {change:+.1f}pp（>10pp 阈值）")
    if abs(change) > 5:
        return MsResult("1.1", "L1", MsVerdict.WARN,
                        f"毛利率 YoY 波动 {change:+.1f}pp（>5pp）")
    return MsResult("1.1", "L1", MsVerdict.PASS, f"毛利率 YoY 波动 {change:+.1f}pp")


def rule_1_2(gm_series: list[float | None], ar: list[float | None],
             ap: list[float | None], revenue: list[float | None]) -> MsResult:
    """毛利率↑ + 应收↑ + 应付↓ 三条件组合。"""
    gm_vals = [g for g in gm_series if g is not None]
    cond_a = len(gm_vals) >= 2 and gm_vals[-1] > gm_vals[-2]

    rev_g = _yoy(revenue[-1] if revenue else None, revenue[-2] if len(revenue) > 1 else None)
    ar_g = _yoy(ar[-1] if ar else None, ar[-2] if len(ar) > 1 else None)
    ap_g = _yoy(ap[-1] if ap else None, ap[-2] if len(ap) > 1 else None)

    cond_b = ar_g is not None and rev_g is not None and ar_g > rev_g
    cond_c = ap_g is not None and ap_g < 0
    n = sum((cond_a, cond_b, cond_c))

    if rev_g is None or ar_g is None:
        return MsResult("1.2", "L1", MsVerdict.SKIP, "应收或营收增速数据不足")
    if n >= 3:
        return MsResult("1.2", "L1", MsVerdict.FAIL,
                        f"三条件全中: 毛利率↑={cond_a} 应收增速>营收={cond_b} 应付↓={cond_c}")
    if n == 2:
        return MsResult("1.2", "L1", MsVerdict.WARN,
                        f"命中 2/3: 毛利率↑={cond_a} 应收增速>营收={cond_b} 应付↓={cond_c}")
    return MsResult("1.2", "L1", MsVerdict.PASS, f"命中 {n}/3 条件")


def rule_1_3() -> MsResult:
    """运费增长 << 收入增长（需年报 PDF 附注，快筛模式跳过）。"""
    return MsResult("1.3", "L1", MsVerdict.SKIP, "深度模式可查（PDF 附注运费明细）")


def rule_1_4(income: list[dict]) -> MsResult:
    """其他业务收入占比突增。"""
    if len(income) < 2:
        return MsResult("1.4", "L1", MsVerdict.SKIP, "利润表年度数据不足")

    ratios = []
    for row in income:
        oth = _f(row, "oth_b_income", "oth_income")
        rev = _f(row, "revenue")
        ratios.append(oth / rev * 100 if oth is not None and rev else None)

    cur, prev = ratios[-1], ratios[-2]
    if cur is None or prev is None:
        return MsResult("1.4", "L1", MsVerdict.SKIP, "其他业务收入字段缺失")
    change = cur - prev
    if cur > 15 or change > 10:
        return MsResult("1.4", "L1", MsVerdict.FAIL,
                        f"其他业务收入占比 {cur:.1f}%（YoY {change:+.1f}pp）")
    if cur > 5 and change > 3:
        return MsResult("1.4", "L1", MsVerdict.WARN,
                        f"其他业务收入占比 {cur:.1f}%（YoY {change:+.1f}pp）")
    return MsResult("1.4", "L1", MsVerdict.PASS, f"其他业务收入占比 {cur:.1f}%")


def rule_1_5(income: list[dict]) -> MsResult:
    """费用率异常下降（相对近3年均值；同行对比分支无同行管道时省略）。"""
    ratios = []
    for row in income:
        rev = _f(row, "revenue")
        exp = sum(_f(row, n) or 0.0 for n in ("sell_exp", "admin_exp", "fin_exp"))
        ratios.append(exp / rev * 100 if rev else None)
    vals = [r for r in ratios if r is not None]
    if len(vals) < 4:
        return MsResult("1.5", "L1", MsVerdict.SKIP, "费用率数据不足（需4年）")

    avg3 = sum(vals[-4:-1]) / 3
    drop = avg3 - vals[-1]
    if drop > 5:
        return MsResult("1.5", "L1", MsVerdict.FAIL,
                        f"费用率较3年均值骤降 {drop:.1f}pp（>5pp）")
    if drop > 3:
        return MsResult("1.5", "L1", MsVerdict.WARN,
                        f"费用率较3年均值下降 {drop:.1f}pp（>3pp）")
    return MsResult("1.5", "L1", MsVerdict.PASS, f"费用率变化 {drop:+.1f}pp")


def rule_1_6(income: list[dict]) -> MsResult:
    """资产/信用减值损失暴增。

    2026-10-19 D 步校准（30 只样本）：
    - 上游 5% 利润占比阈值过严：23/30 触发 FAIL，含五粮液/泸州老窖等
      减值仅占利润 0.3-0.4% 的公司（基数小导致 YoY 巨大）；
    - FAIL：减值占利润 >10%，或 YoY>100% 且占利润 >5%（有实质利润影响）；
    - WARN：占利润 5-10%，或 YoY>50% 且占利润 >1%；
    - 占利润 <1% 的 YoY 波动视为噪声，PASS。
    """
    if len(income) < 2:
        return MsResult("1.6", "L1", MsVerdict.SKIP, "利润表年度数据不足")

    def impair(row: dict) -> float | None:
        a = _f(row, "assets_impair_loss", "asset_impair_loss")
        c = _f(row, "credit_impa_loss", "credit_impair_loss")
        if a is None and c is None:
            return None
        return abs(a or 0.0) + abs(c or 0.0)

    cur, prev = impair(income[-1]), impair(income[-2])
    np_cur = _f(income[-1], "n_income_attr_p")
    if cur is None or prev is None:
        return MsResult("1.6", "L1", MsVerdict.SKIP, "减值损失字段缺失")

    yoy = cur / prev - 1.0 if prev > 0 else None
    to_profit = cur / abs(np_cur) * 100 if np_cur else None
    yoy_txt = yoy if yoy is None else f"{yoy:.0%}"
    profit_txt = to_profit if to_profit is None else f"{to_profit:.1f}%"

    if to_profit is None:
        if yoy is not None and yoy > 1.0:
            return MsResult("1.6", "L1", MsVerdict.FAIL, f"减值 YoY {yoy_txt}")
        if yoy is not None and yoy > 0.5:
            return MsResult("1.6", "L1", MsVerdict.WARN, f"减值 YoY {yoy_txt}")
        return MsResult("1.6", "L1", MsVerdict.PASS, f"减值 YoY {yoy_txt}")

    if to_profit > 10 or (yoy is not None and yoy > 1.0 and to_profit > 5):
        return MsResult("1.6", "L1", MsVerdict.FAIL,
                        f"减值 YoY {yoy_txt}，占利润 {profit_txt}")
    if to_profit > 5 or (yoy is not None and yoy > 0.5 and to_profit > 1):
        return MsResult("1.6", "L1", MsVerdict.WARN,
                        f"减值 YoY {yoy_txt}，占利润 {profit_txt}")
    return MsResult("1.6", "L1", MsVerdict.PASS,
                    f"减值 YoY {yoy_txt}，占利润 {profit_txt}")


# ---------- Layer 2: 现金流信号 ----------

def rule_2_1(ocf: list[float | None], icf: list[float | None]) -> MsResult:
    """经营 CF 优异 + 投资 CF 持续大额为负（扩张烧钱过度）。"""
    years_hit = 0
    for o, i in zip(ocf, icf):
        if o is not None and i is not None and o > 0 and abs(i) > o * 0.8:
            years_hit += 1
    if len([o for o in ocf if o is not None]) < 3:
        return MsResult("2.1", "L2", MsVerdict.SKIP, "现金流数据不足")

    if years_hit >= 4:
        return MsResult("2.1", "L2", MsVerdict.FAIL, f"近10年 {years_hit} 年投资CF吞掉经营CF")
    if years_hit in (2, 3):
        return MsResult("2.1", "L2", MsVerdict.WARN, f"{years_hit} 年投资CF超经营CF八成")
    return MsResult("2.1", "L2", MsVerdict.PASS, f"{years_hit} 年投资CF超经营CF八成")


def rule_2_2(ocf: list[float | None]) -> MsResult:
    """经营 CF 持续为负。"""
    vals = [o for o in ocf if o is not None]
    if len(vals) < 5:
        return MsResult("2.2", "L2", MsVerdict.SKIP, "现金流数据不足（需5年）")

    last5 = vals[-5:]
    neg = sum(1 for v in last5 if v < 0)
    # 连续为负的最长年数
    streak = best = 0
    for v in vals:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)

    if best >= 3:
        return MsResult("2.2", "L2", MsVerdict.FAIL, f"最长连续 {best} 年经营CF为负")
    if neg >= 2:
        return MsResult("2.2", "L2", MsVerdict.WARN, f"近5年 {neg} 年经营CF为负")
    return MsResult("2.2", "L2", MsVerdict.PASS, f"近5年 {neg} 年经营CF为负")


def rule_2_3(balance_latest: dict, interest_exp: float | None, base_rate: float) -> MsResult:
    """高现金 + 高息借债（存贷双高，经典造假信号）。

    2026-10 验收/D 步修正：
    - 隐含利率用 **利息支出 int_exp**，不是财务费用净额 fin_exp（后者含利息收入，
      美的 fin_exp=-59 亿会算出 9% 假高利率；int_exp 仅 27.5 万）；
    - 有息负债为 0/占总资产 <1% 时直接 PASS，不再因字段或低负债 SKIP；
    - 有息负债补入一年内到期非流动负债。
    """
    cash = _f(balance_latest, "money_cap")
    if cash is None:
        return MsResult("2.3", "L2", MsVerdict.SKIP, "货币资金字段缺失")
    ibd = sum(_f(balance_latest, n) or 0.0 for n in
              ("st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"))
    if ibd <= 0:
        return MsResult("2.3", "L2", MsVerdict.PASS, "无有息负债，存贷双高不适用")
    total_assets = _f(balance_latest, "total_assets")
    if total_assets and total_assets > 0 and ibd / total_assets < 0.01:
        return MsResult("2.3", "L2", MsVerdict.PASS,
                        f"有息负债/总资产仅 {ibd / total_assets:.2%}，存贷双高不适用")
    if interest_exp is None:
        return MsResult("2.3", "L2", MsVerdict.SKIP, "有息负债存在但利息支出字段缺失")

    implied = abs(interest_exp) / ibd * 100
    if cash > ibd and implied > base_rate + 4:
        return MsResult("2.3", "L2", MsVerdict.FAIL,
                        f"存贷双高: 现金>有息负债且隐含利率 {implied:.1f}%（基准 {base_rate}%+4pp）")
    if cash > ibd * 0.5 and implied > base_rate + 2:
        return MsResult("2.3", "L2", MsVerdict.WARN,
                        f"现金充裕仍较高息借债: 隐含利率 {implied:.1f}%")
    return MsResult("2.3", "L2", MsVerdict.PASS, f"隐含利率 {implied:.1f}%")


# ---------- Layer 3: 资产负债表信号 ----------

def rule_3_1(ar: list[float | None], revenue: list[float | None]) -> MsResult:
    """应收增速 > 收入增速。"""
    rev_g = _yoy(revenue[-1] if revenue else None, revenue[-2] if len(revenue) > 1 else None)
    ar_g = _yoy(ar[-1] if ar else None, ar[-2] if len(ar) > 1 else None)
    ar_cur, rev_cur = ar[-1] if ar else None, revenue[-1] if revenue else None

    if rev_g is None or ar_g is None or ar_cur is None or not rev_cur:
        return MsResult("3.1", "L3", MsVerdict.SKIP, "应收/营收数据不足")
    if ar_cur < rev_cur * 0.05:
        return MsResult("3.1", "L3", MsVerdict.PASS, "应收账款极小（<营收5%）")

    if rev_g < 0 and ar_g > 0:
        return MsResult("3.1", "L3", MsVerdict.WARN, "营收下降但应收增长")
    ratio = ar_g / rev_g if rev_g > 0 else None
    if ratio is not None and ratio > 2.0:
        return MsResult("3.1", "L3", MsVerdict.FAIL, f"应收增速/营收增速 = {ratio:.1f}（>2.0）")
    if ratio is not None and ratio > 1.5:
        return MsResult("3.1", "L3", MsVerdict.WARN, f"应收增速/营收增速 = {ratio:.1f}（>1.5）")
    return MsResult("3.1", "L3", MsVerdict.PASS,
                    f"应收增速 {ar_g:.0%} vs 营收增速 {rev_g:.0%}")


def rule_3_2(inv_turn: list[float | None], gm_series: list[float | None]) -> MsResult:
    """存货周转↓ + 毛利率↑（造假组合信号，FAIL 时触发组合加分）。"""
    turns = [t for t in inv_turn if t is not None]
    gms = [g for g in gm_series if g is not None]
    if len(turns) < 2 or len(gms) < 2 or turns[-2] == 0:
        return MsResult("3.2", "L3", MsVerdict.SKIP, "存货周转或毛利率数据不足")

    turn_change = turns[-1] / turns[-2] - 1.0
    gm_change = gms[-1] - gms[-2]

    if turn_change < -0.20 and gm_change > 3:
        return MsResult("3.2", "L3", MsVerdict.FAIL,
                        f"存货周转 {turn_change:.0%} 且毛利率 +{gm_change:.1f}pp")
    if turn_change < -0.10 and gm_change > 0:
        return MsResult("3.2", "L3", MsVerdict.WARN,
                        f"存货周转 {turn_change:.0%} 且毛利率 +{gm_change:.1f}pp")
    return MsResult("3.2", "L3", MsVerdict.PASS,
                    f"存货周转 {turn_change:+.0%}，毛利率 {gm_change:+.1f}pp")


def rule_3_3(cip: list[float | None], fix: list[float | None]) -> MsResult:
    """在建工程持续增长但不转固（连续年份，非累计年数）。"""
    streak = 0
    max_streak = 0
    for i in range(1, min(len(cip), len(fix))):
        c, p, f_c, f_p = cip[i], cip[i - 1], fix[i], fix[i - 1]
        if None in (c, p, f_c, f_p) or p <= 0:
            streak = 0
            continue
        cip_g = c / p - 1.0
        fix_g = f_c / f_p - 1.0 if f_p > 0 else 1.0
        if cip_g > 0.30 and fix_g < cip_g * 0.5:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if not cip or cip[-1] is None:
        return MsResult("3.3", "L3", MsVerdict.SKIP, "在建工程数据缺失")
    if max_streak >= 3:
        return MsResult("3.3", "L3", MsVerdict.FAIL, f"连续 {max_streak} 年在建工程高增不转固")
    if max_streak >= 1:
        return MsResult("3.3", "L3", MsVerdict.WARN, f"{max_streak} 年在建工程高增不转固")
    return MsResult("3.3", "L3", MsVerdict.PASS, "在建工程无异常")


def rule_3_4(balance: list[dict], total_assets: list[float | None]) -> MsResult:
    """长期待摊费用大增。"""
    if len(balance) < 2:
        return MsResult("3.4", "L3", MsVerdict.SKIP, "资产负债表年度数据不足")
    cur = _f(balance[-1], "lt_amor_exp")
    prev = _f(balance[-2], "lt_amor_exp")
    ta = total_assets[-1] if total_assets else None
    if cur is None or prev is None:
        return MsResult("3.4", "L3", MsVerdict.SKIP, "长期待摊费用字段缺失")
    if ta and cur < ta * 0.01:
        return MsResult("3.4", "L3", MsVerdict.PASS, "长期待摊费用极小（<总资产1%）")

    yoy = cur / prev - 1.0 if prev > 0 else None
    if yoy is None:
        return MsResult("3.4", "L3", MsVerdict.SKIP, "长期待摊费用基期为零")
    if yoy > 1.0:
        return MsResult("3.4", "L3", MsVerdict.FAIL, f"长期待摊费用 YoY {yoy:.0%}")
    if yoy > 0.5:
        return MsResult("3.4", "L3", MsVerdict.WARN, f"长期待摊费用 YoY {yoy:.0%}")
    return MsResult("3.4", "L3", MsVerdict.PASS, f"长期待摊费用 YoY {yoy:.0%}")


def rule_3_5(income_latest: dict, balance_latest: dict,
             peers: dict | None) -> MsResult:
    """坏账计提比例低于同行（3.5，2026-10-19 实现）。

    公司口径：|信用减值损失| / 应收账款 ×100；同行口径用同行业年报的
    income.credit_impa_loss + balancesheet.accounts_receiv 计算
    **加权计提率**（总损失/总应收，C 步校准：比小样本中位数更稳健）。
    应收账款极小（<总资产1%）或同行样本不足 → SKIP；低于同行→WARN，
    低于同行 50%→FAIL（上游规则）。
    """
    ar = _f(balance_latest, "accounts_receiv")
    loss = _f(income_latest, "credit_impa_loss")
    total_assets = _f(balance_latest, "total_assets")
    if ar is None or loss is None or ar <= 0:
        return MsResult("3.5", "L3", MsVerdict.SKIP,
                        "应收账款或信用减值损失字段缺失")
    if total_assets and total_assets > 0 and ar / total_assets < 0.01:
        return MsResult("3.5", "L3", MsVerdict.SKIP,
                        f"应收账款占总资产仅 {ar / total_assets:.2%}，坏账比例不具可比性")
    peer_median = (peers or {}).get("bad_debt_ratio_agg")
    if peer_median is None:
        peer_median = (peers or {}).get("bad_debt_ratio_median")
    n_peers = (peers or {}).get("n_bad_debt_peers") or 0
    if peer_median is None or n_peers < 3:
        return MsResult("3.5", "L3", MsVerdict.SKIP,
                        "同行坏账计提比例数据不足（需≥3家）")
    if peer_median <= 0:
        return MsResult("3.5", "L3", MsVerdict.SKIP,
                        "同行坏账计提比例中位数为 0，比较无意义")

    ratio = abs(loss) / ar * 100.0
    detail = f"坏账计提比例 {ratio:.2f}% vs 同行加权 {peer_median:.2f}%"
    if ratio < peer_median * 0.5:
        return MsResult("3.5", "L3", MsVerdict.FAIL, detail + "（低于同行 50%）")
    if ratio < peer_median:
        return MsResult("3.5", "L3", MsVerdict.WARN, detail + "（低于同行中位数）")
    return MsResult("3.5", "L3", MsVerdict.PASS, detail)


# ---------- Layer 4: 交叉验证 ----------

def rule_4_1(ocf: list[float | None], np_: list[float | None]) -> MsResult:
    """经营 CF / 净利润 < 1（最核心验证）。"""
    pairs = [(o, n) for o, n in zip(ocf, np_) if o is not None and n is not None and n > 0]
    if len(pairs) < 5:
        return MsResult("4.1", "L4", MsVerdict.SKIP, "数据不足（需5年正利润）")

    ratios = [o / n for o, n in pairs]
    below = sum(1 for r in ratios[-5:] if r < 1)
    streak = best = 0
    for r in ratios:
        streak = streak + 1 if r < 1 else 0
        best = max(best, streak)

    if best >= 3:
        return MsResult("4.1", "L4", MsVerdict.FAIL, f"连续 {best} 年 CF/净利 < 1")
    if below >= 2:
        return MsResult("4.1", "L4", MsVerdict.WARN, f"近5年 {below} 年 CF/净利 < 1")
    return MsResult("4.1", "L4", MsVerdict.PASS,
                    f"近5年 CF/净利均值 {sum(ratios[-5:]) / len(ratios[-5:]):.2f}")


def rule_4_2(cash_recv: list[float | None], revenue: list[float | None]) -> MsResult:
    """销售收现 / 营收（含 13% 增值税调整）。"""
    pairs = [(c, r) for c, r in zip(cash_recv, revenue)
             if c is not None and r is not None and r > 0]
    if len(pairs) < 3:
        return MsResult("4.2", "L4", MsVerdict.SKIP, "收现数据不足")

    ratios = [c / (r * 1.13) for c, r in pairs][-3:]
    below_08 = sum(1 for r in ratios if r < 0.8)
    if below_08 >= 2:
        return MsResult("4.2", "L4", MsVerdict.FAIL,
                        f"近3年 {below_08} 年收现比 < 0.8（最低 {min(ratios):.2f}）")
    if min(ratios) < 0.9:
        return MsResult("4.2", "L4", MsVerdict.WARN,
                        f"近3年最低收现比 {min(ratios):.2f}（<0.9）")
    return MsResult("4.2", "L4", MsVerdict.PASS, f"近3年收现比均值 {sum(ratios) / len(ratios):.2f}")


def rule_4_3(total_assets: list[float | None], revenue: list[float | None],
             np_: list[float | None]) -> MsResult:
    """利润膨胀 → 资产膨胀（虚增利润必伴随虚增资产）。

    2026-10 修正：营收负增长时不能用 `a_g > r_g*3`（负数比较恒真），
    要求 r_g>0；按"连续 2+ 年"累计连续段（上游 doc：连续 2+ 年）。
    """
    if len(total_assets) < 3:
        return MsResult("4.3", "L4", MsVerdict.SKIP, "资产数据不足")

    flags = 0
    max_streak = 0
    for i in range(1, len(total_assets)):
        a_g = _yoy(total_assets[i], total_assets[i - 1])
        r_g = _yoy(revenue[i] if i < len(revenue) else None,
                   revenue[i - 1] if i - 1 < len(revenue) else None)
        p_g = _yoy(np_[i] if i < len(np_) else None,
                   np_[i - 1] if i - 1 < len(np_) else None)
        if None in (a_g, r_g) or r_g is None or r_g <= 0:
            flags = 0
            continue
        profit_up = p_g is not None and p_g > 0
        if a_g > r_g * 3 and profit_up:
            flags += 1
            max_streak = max(max_streak, flags)
        else:
            flags = 0

    np_g = _yoy(np_[-1] if np_ else None, np_[-2] if len(np_) > 1 else None)
    a_g = _yoy(total_assets[-1], total_assets[-2])
    r_g = _yoy(revenue[-1] if revenue else None, revenue[-2] if len(revenue) > 1 else None)

    if max_streak >= 2:
        return MsResult("4.3", "L4", MsVerdict.FAIL, f"连续 {max_streak} 年资产增速>营收3倍且利润增长")
    if None not in (a_g, r_g) and r_g > 0 and np_g and a_g > r_g * 2 and np_g > 0:
        return MsResult("4.3", "L4", MsVerdict.WARN,
                        f"资产增速 {a_g:.0%} > 营收增速 {r_g:.0%} 的2倍且利润增长")
    return MsResult("4.3", "L4", MsVerdict.PASS, "资产扩张与营收匹配")


def rule_4_4(income_latest: dict) -> MsResult:
    """核心利润 vs 净利润背离。

    核心利润 = 营收 − 营业成本 − 税金及附加 − 三费。
    口径说明（2026-10 校准）：
    - 税金及附加字段真实名为 ``biz_tax_surchg``（历史 operate_tax_add/biz_tax_sur 不存在）；
    - 核心利润是少数股东损益前口径，必须与**总净利润 n_income** 比较，否则少数股东
      占比高的公司（神华：核心/归母 40.3% vs 核心/总净利 18.1%）会系统性误报；
      n_income 缺失时才回退 n_income_attr_p。
    """
    rev = _f(income_latest, "revenue")
    cost = _f(income_latest, "oper_cost")
    tax = _f(income_latest, "biz_tax_surchg")
    sell = _f(income_latest, "sell_exp")
    admin = _f(income_latest, "admin_exp")
    fin = _f(income_latest, "fin_exp")
    np_all = _f(income_latest, "n_income", "n_income_attr_p")
    if None in (rev, cost, tax, sell, admin, fin, np_all) or np_all == 0:
        return MsResult("4.4", "L4", MsVerdict.SKIP, "核心利润字段缺失（含税金及附加）")

    core = rev - cost - tax - sell - admin - fin
    divergence = abs(core - np_all) / abs(np_all) * 100
    if divergence > 40:
        return MsResult("4.4", "L4", MsVerdict.FAIL,
                        f"核心利润与净利背离 {divergence:.0f}%（>40%）")
    if divergence > 20:
        return MsResult("4.4", "L4", MsVerdict.WARN, f"核心利润与净利背离 {divergence:.0f}%")
    return MsResult("4.4", "L4", MsVerdict.PASS, f"核心利润与净利背离 {divergence:.0f}%")


def rule_4_5(np_: list[float | None], fcf: list[float | None]) -> MsResult:
    """净利润增长 + FCF 持续为负。"""
    years_hit = 0
    for i in range(1, min(len(np_), len(fcf))):
        p_cur, p_prev = np_[i], np_[i - 1]
        f = fcf[i]
        if None in (p_cur, p_prev, f):
            continue
        if p_cur > 0 and p_cur > p_prev and f < 0:
            years_hit += 1
    if not any(f is not None for f in fcf):
        return MsResult("4.5", "L4", MsVerdict.SKIP, "FCF 数据缺失")

    if years_hit >= 3:
        return MsResult("4.5", "L4", MsVerdict.FAIL, f"{years_hit} 年利润增长但 FCF 为负")
    if years_hit == 2:
        return MsResult("4.5", "L4", MsVerdict.WARN, f"{years_hit} 年利润增长但 FCF 为负")
    return MsResult("4.5", "L4", MsVerdict.PASS, f"{years_hit} 年利润增长但 FCF 为负")
