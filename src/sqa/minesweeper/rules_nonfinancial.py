"""排雷规则 L0/L5/L6/S：门槛审计 / 非财务信号 / 行业特有 / 本系统补充。"""
from __future__ import annotations

from sqa.minesweeper.models import MsResult, MsVerdict

# 农林牧渔高风险行业关键词（上游 L6.1；注意排除已在本系统范围外的医药）
_AGRI_KEYWORDS = ("农业", "林业", "渔业", "牧业", "畜牧", "养殖", "种植", "饲料", "水产", "农业综合")


def _f(row: dict, *names: str) -> float | None:
    for n in names:
        v = row.get(n)
        if v is not None:
            return float(v)
    return None


# ---------- Layer 0: 门槛（一票否决） ----------

def rule_0_1(audit_rows: list[dict]) -> MsResult:
    """审计意见：非无保留 → 直接排除。

    PASS: 含"无保留"（覆盖 标准无保留 / 无保留 / 带强调事项段的无保留）；
    FAIL: 保留意见 / 无法表示意见 / 否定意见 等其余一切。
    """
    if not audit_rows:
        return MsResult("0.1", "L0", MsVerdict.SKIP, "审计数据缺失")
    latest = audit_rows[-1]
    result = str(latest.get("audit_result") or "")
    if not result:
        return MsResult("0.1", "L0", MsVerdict.SKIP, "审计意见字段为空")
    if "无保留" in result:
        return MsResult("0.1", "L0", MsVerdict.PASS, result)
    return MsResult("0.1", "L0", MsVerdict.FAIL, f"非标审计意见: {result}")


def rule_0_2(audit_rows: list[dict]) -> MsResult:
    """年报按时披露（次年 4 月 30 日前）。"""
    if not audit_rows:
        return MsResult("0.2", "L0", MsVerdict.SKIP, "审计数据缺失")
    latest = audit_rows[-1]
    end_date, ann_date = str(latest.get("end_date") or ""), str(latest.get("ann_date") or "")
    if len(end_date) != 8 or len(ann_date) != 8:
        return MsResult("0.2", "L0", MsVerdict.SKIP, "披露日期字段缺失")
    deadline = f"{int(end_date[:4]) + 1}0430"
    if ann_date <= deadline:
        return MsResult("0.2", "L0", MsVerdict.PASS,
                        f"年报 {ann_date} 披露（截止 {deadline}）")
    return MsResult("0.2", "L0", MsVerdict.FAIL,
                    f"年报逾期披露: {ann_date} > 截止 {deadline}")


# ---------- Layer 5: 非财务信号 ----------

def _normalize_agency(name: str) -> str:
    """审计所名称归一化：同一事务所的不同写法不应计为换所。

    处理：全角括号→半角、去空白、去"（特殊普通合伙）"后缀、
    "会计师事务所"/"事务所"后缀统一（同一所常有 6 种变体——金标准教训）。
    """
    s = name.replace("（", "(").replace("）", ")")
    s = s.replace("(特殊普通合伙)", "").replace("特殊普通合伙", "")
    s = s.replace("会计师事务所", "事务所")
    if s.endswith("事务所"):
        s = s[:-3]
    return "".join(s.split())


# 换所检测只看最近 10 个年报：全历史窗口会让几乎所有老公司都触发 FAIL
# （事务所行业并购改名潮 + 正常轮换），规则退化为噪声（茅台案例校准）
_AGENCY_WINDOW = 10


def rule_5_1(audit_rows: list[dict]) -> MsResult:
    """更换审计机构（名称归一化后比较，限最近 10 个年报）。"""
    recent = audit_rows[-_AGENCY_WINDOW:]
    agencies = []
    for r in recent:
        a = _normalize_agency(str(r.get("audit_agency") or ""))
        if a and (not agencies or agencies[-1] != a):
            agencies.append(a)
    if len(agencies) < 2:
        return MsResult("5.1", "L5", MsVerdict.SKIP if not agencies else MsVerdict.PASS,
                        f"近{_AGENCY_WINDOW}年事务所记录 {len(agencies)} 条")

    changes = len(agencies) - 1
    if changes >= 2:
        return MsResult("5.1", "L5", MsVerdict.FAIL, f"近{_AGENCY_WINDOW}年更换审计机构 {changes} 次")
    return MsResult("5.1", "L5", MsVerdict.WARN, f"近{_AGENCY_WINDOW}年更换审计机构 1 次")


def rule_5_2(holder_ratio_series: list[float | None]) -> MsResult:
    """大股东减持（第一大股东持股比例趋势，近似口径）。"""
    vals = [v for v in holder_ratio_series if v is not None]
    if len(vals) < 2:
        return MsResult("5.2", "L5", MsVerdict.SKIP, "股东持股数据不足")

    drop = vals[0] - vals[-1]  # 首期到末期
    declining = all(vals[i + 1] <= vals[i] for i in range(len(vals) - 1))
    if drop > 5:
        return MsResult("5.2", "L5", MsVerdict.FAIL, f"第一大股东持股下降 {drop:.1f}pp")
    if declining and drop > 0.5:
        return MsResult("5.2", "L5", MsVerdict.WARN,
                        f"第一大股东持股连续下降（累计 {drop:.1f}pp）")
    return MsResult("5.2", "L5", MsVerdict.PASS, f"第一大股东持股稳定（变化 {drop:+.1f}pp）")


def rule_5_3() -> MsResult:
    return MsResult("5.3", "L5", MsVerdict.SKIP, "深度模式可查（PDF 高管变动）")


def rule_5_4() -> MsResult:
    return MsResult("5.4", "L5", MsVerdict.SKIP, "深度模式可查（PDF 独董辞职）")


def rule_5_5() -> MsResult:
    return MsResult("5.5", "L5", MsVerdict.SKIP, "深度模式可查（PDF 客户集中度）")


def rule_5_6() -> MsResult:
    return MsResult("5.6", "L5", MsVerdict.SKIP, "深度模式可查（PDF 跨行业收购）")


def rule_5_7(balance_latest: dict) -> MsResult:
    """商誉过大。"""
    gw = _f(balance_latest, "goodwill")
    equity = _f(balance_latest, "total_hldr_eqy_exc_min_int", "total_hldr_eqy")
    if equity is None or equity <= 0:
        return MsResult("5.7", "L5", MsVerdict.SKIP, "净资产字段缺失")
    if not gw:
        return MsResult("5.7", "L5", MsVerdict.PASS, "无商誉")

    ratio = gw / equity * 100
    if ratio > 40:
        return MsResult("5.7", "L5", MsVerdict.FAIL, f"商誉/净资产 {ratio:.0f}%（>40%）")
    if ratio > 20:
        return MsResult("5.7", "L5", MsVerdict.WARN, f"商誉/净资产 {ratio:.0f}%（>20%）")
    return MsResult("5.7", "L5", MsVerdict.PASS, f"商誉/净资产 {ratio:.0f}%")


def rule_5_8(balance: list[dict], total_assets: list[float | None]) -> MsResult:
    """其他应收款异常（关联方资金占用藏身之处）。

    小额豁免（对齐上游 3.4 的"金额极小"先例）：占比 ≤0.5% 时无论增速
    最多 WARN——0.3% 的基数上 YoY 55% 属噪声，不构成 FAIL 证据
    （金标准教训：双汇误报）。
    """
    if len(balance) < 2:
        return MsResult("5.8", "L5", MsVerdict.SKIP, "资产负债表年度数据不足")
    oth = _f(balance[-1], "oth_receiv")
    oth_prev = _f(balance[-2], "oth_receiv")
    ta = total_assets[-1] if total_assets else None
    if oth is None or oth_prev is None or ta is None:
        return MsResult("5.8", "L5", MsVerdict.SKIP, "其他应收款字段缺失")

    ratio = oth / ta * 100
    yoy = oth / oth_prev - 1.0 if oth_prev > 0 else None
    yoy_str = yoy if yoy is None else f"{yoy:.0%}"
    if ratio > 5 or (yoy is not None and yoy > 0.5 and ratio > 0.5):
        return MsResult("5.8", "L5", MsVerdict.FAIL,
                        f"其他应收款/总资产 {ratio:.1f}%，YoY {yoy_str}")
    if ratio > 3 or (yoy is not None and yoy > 0.3):
        return MsResult("5.8", "L5", MsVerdict.WARN,
                        f"其他应收款/总资产 {ratio:.1f}%，YoY {yoy_str}（小额或增速项）")
    return MsResult("5.8", "L5", MsVerdict.PASS, f"其他应收款/总资产 {ratio:.1f}%")


def rule_5_9() -> MsResult:
    return MsResult("5.9", "L5", MsVerdict.SKIP, "深度模式可查（PDF 监管处罚关键词）")


# ---------- Layer 6: 行业特有 ----------

def rule_6_1(industry: str) -> MsResult:
    """农林牧渔：生物资产难以审计，历史上造假高发区。"""
    ind = industry or ""
    if any(kw in ind for kw in _AGRI_KEYWORDS):
        return MsResult("6.1", "L6", MsVerdict.WARN, f"高风险行业: {ind}")
    return MsResult("6.1", "L6", MsVerdict.PASS, f"行业: {ind}")


def rule_6_2() -> MsResult:
    return MsResult("6.2", "L6", MsVerdict.SKIP, "深度模式可查（PDF 研发资本化）")


# ---------- Layer S: 本系统补充（02 号文档 §3） ----------

def rule_s1(pledge_ratio: float | None) -> MsResult:
    """S1 股权质押率（pledge_stat 全公司口径，作为大股东质押代理）。"""
    if pledge_ratio is None:
        return MsResult("S1", "S", MsVerdict.SKIP, "质押数据缺失")
    if pledge_ratio > 50:
        return MsResult("S1", "S", MsVerdict.FAIL, f"质押比例 {pledge_ratio:.0f}%（>50%）")
    if pledge_ratio > 30:
        return MsResult("S1", "S", MsVerdict.WARN, f"质押比例 {pledge_ratio:.0f}%（30-50%）")
    return MsResult("S1", "S", MsVerdict.PASS, f"质押比例 {pledge_ratio:.0f}%")


def rule_s2(balance_latest: dict) -> MsResult:
    """S2 短期偿债缺口：货币资金 / 短期有息负债。

    口径（2026-10 校准）：
    - 一年内到期非流动负债真实字段为 ``non_cur_liab_due_1y``；
    - 短期有息负债为 0（无短债）应视为最佳覆盖 → PASS，而不是 SKIP；
    - 有息短债极小（<总资产 3%）同样视为无实质缺口 → PASS。
    """
    cash = _f(balance_latest, "money_cap")
    st_debt = _f(balance_latest, "st_borr") or 0.0
    ncl_1y = _f(balance_latest, "non_cur_liab_due_1y", "nca_within_1y_liab",
                "ncl_1y", "non_cur_liab_1y") or 0.0
    ta = _f(balance_latest, "total_assets")
    short_debt = st_debt + ncl_1y

    if cash is None:
        return MsResult("S2", "S", MsVerdict.SKIP, "货币资金字段缺失")
    if short_debt <= 0:
        return MsResult("S2", "S", MsVerdict.PASS, "无短期有息负债")
    if ta and short_debt < ta * 0.03:
        return MsResult("S2", "S", MsVerdict.PASS, "短期有息负债极小（<总资产3%）")

    coverage = cash / short_debt
    if coverage < 1.0:
        return MsResult("S2", "S", MsVerdict.FAIL, f"货币资金/短债 = {coverage:.2f}")
    if coverage < 1.5:
        return MsResult("S2", "S", MsVerdict.WARN, f"货币资金/短债 = {coverage:.2f}")
    return MsResult("S2", "S", MsVerdict.PASS, f"货币资金/短债 = {coverage:.2f}")


def rule_s3(income_latest: dict) -> MsResult:
    """S3 利息保障倍数：EBIT / 利息支出。"""
    ebit = _f(income_latest, "total_profit")
    interest = _f(income_latest, "int_exp", "interest_exp")
    rev = _f(income_latest, "revenue")
    if ebit is None or interest is None or interest <= 0:
        if interest is None and _f(income_latest, "fin_exp") is not None:
            interest = abs(_f(income_latest, "fin_exp") or 0.0)
        else:
            return MsResult("S3", "S", MsVerdict.SKIP, "利息支出字段缺失")
    if rev and interest < rev * 0.005:
        return MsResult("S3", "S", MsVerdict.PASS, "利息支出极小（<营收0.5%）")

    multiple = ebit / interest
    if multiple < 3:
        return MsResult("S3", "S", MsVerdict.FAIL, f"利息保障倍数 {multiple:.1f}（<3）")
    if multiple < 6:
        return MsResult("S3", "S", MsVerdict.WARN, f"利息保障倍数 {multiple:.1f}（3-6）")
    return MsResult("S3", "S", MsVerdict.PASS, f"利息保障倍数 {multiple:.1f}")


def rule_s4(pledge_ratio: float | None, holder_drop_pp: float | None) -> MsResult:
    """S4 组合信号：高质押 + 减持（命中计 FAIL 供组合加分，非独立否决）。"""
    if pledge_ratio is None or holder_drop_pp is None:
        return MsResult("S4", "S", MsVerdict.SKIP, "质押或股东动向数据缺失")
    if pledge_ratio > 30 and holder_drop_pp > 2:
        return MsResult("S4", "S", MsVerdict.FAIL,
                        f"质押 {pledge_ratio:.0f}% 且大股东持股降 {holder_drop_pp:.1f}pp")
    return MsResult("S4", "S", MsVerdict.PASS,
                    f"质押 {pledge_ratio:.0f}%，大股东持股变化 {holder_drop_pp:+.1f}pp")
