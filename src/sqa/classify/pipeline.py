"""分类判定流水线（01 号文档 §4 总图）：取数 → 范围排除 → 周期 → 红利 → 风格 → 标签。"""
from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime

from sqa.classify.cycle import is_cycle_industry, is_cycle_quantitative
from sqa.classify.dividend import (
    evaluate_dividend, rule_d1, rule_d2, rule_d3, rule_d4,
)
from sqa.classify.exclusions import exclusion_reason
from sqa.classify.industry_tag import tag_industry
from sqa.classify.models import ClassificationOutput, Verdict
from sqa.classify.style import decide_style
from sqa.config import Thresholds, load_thresholds, risk_free_rate
from sqa.data.fetchers import (
    fetch_annual_cashflow, fetch_annual_income, fetch_annual_indicator,
    fetch_basic, fetch_daily_basic, fetch_dividend_records, resolve_ts_code,
)
from sqa.data.tushare_client import SqaDataError, TushareClient
from sqa.quality.metrics import avg_payout, cagr_last_n


def classify_stock(symbol: str, cfg: Thresholds | None = None,
                   client: TushareClient | None = None,
                   refresh: bool = False) -> dict:
    """主入口：股票代码 → 01 号文档 §7 的 JSON 结构（dict）。"""
    cfg = cfg or load_thresholds()
    if client is None:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))

    ts_code = resolve_ts_code(symbol)
    basic = fetch_basic(client, ts_code, refresh)

    # ---- 第 0 步：范围排除 ----
    reason = exclusion_reason(basic.name, basic.industry)
    if reason:
        return asdict(ClassificationOutput(
            symbol=basic.symbol, name=basic.name, style="OUT_OF_SCOPE",
            confidence="high", warnings=(reason,),
            evidence={"exclusion": reason},
        ))

    # ---- 取数（缺口一律 None，由规则层降级为 SKIP）----
    this_year = datetime.now().year
    indicators = fetch_annual_indicator(client, ts_code, cfg.history_years, refresh)
    income = fetch_annual_income(client, ts_code, cfg.history_years, refresh)
    try:
        cashflow = fetch_annual_cashflow(client, ts_code, cfg.history_years, refresh)
    except SqaDataError:
        cashflow = {}
    try:
        daily = fetch_daily_basic(client, ts_code, cfg.val_pctl_window_years, refresh)
    except SqaDataError:
        daily = []
    try:
        divs = fetch_dividend_records(client, ts_code, refresh)
    except SqaDataError:
        divs = {}

    years = sorted(set(indicators) | set(income))
    margins = [indicators.get(y, {}).get("gm") for y in years]
    profits = [income.get(y, {}).get("n_income") for y in years]
    data_years = sum(
        1 for y in years
        if indicators.get(y, {}).get("gm") is not None
        or income.get(y, {}).get("n_income") is not None
    )

    # ---- 第 1 步：周期性 ----
    r1_hit = is_cycle_industry(basic.industry)
    r2 = is_cycle_quantitative(margins, profits, cfg)
    is_cycle = r1_hit or r2.verdict is Verdict.HIT

    # ---- 第 2 步：红利属性（与主风格独立，周期股也可叠加）----
    dv_ratios = _year_end_dv_ratios(daily, this_year, n=3)
    streak = _consecutive_div_years(divs, this_year)
    payout = avg_payout(
        divs, {y: indicators.get(y, {}).get("eps") for y in indicators},
        this_year, n=3)
    cum_div, cum_fcf = _cum_div_and_fcf(divs, daily, cashflow, indicators, this_year, n=3)
    div_rules = [
        rule_d1(dv_ratios, risk_free_rate(client), cfg),
        rule_d2(streak, cfg),
        rule_d3(payout, cfg),
        rule_d4(cum_div, cum_fcf, cfg),
    ]
    has_dividend = evaluate_dividend(div_rules)

    # ---- 第 3 步：主风格 ----
    signals: tuple[str, ...] = ()
    if is_cycle:
        style = "cyclical"
        style_evidence = {"note": "周期命中，G1/V1 跳过（防假成长/假价值）"}
    else:
        rev_cagr = cagr_last_n([income.get(y, {}).get("revenue") for y in years], 3)
        np_cagr = cagr_last_n([indicators.get(y, {}).get("profit_dedt") for y in years], 3)
        pe_pctl, pb_pctl = _valuation_percentiles(daily)
        style, signals = decide_style(rev_cagr, np_cagr, pe_pctl, pb_pctl, cfg)
        style_evidence = {
            "rev_cagr": _fmt_pct(rev_cagr), "np_dedt_cagr": _fmt_pct(np_cagr),
            "pe_pctl": _fmt_pct(pe_pctl), "pb_pctl": _fmt_pct(pb_pctl),
        }

    # ---- 第 4 步：行业标签 ----
    tag = tag_industry(basic.industry)

    # ---- 置信度与警示 ----
    if is_cycle:
        confidence = "high" if r1_hit else "medium"
    elif data_years >= 5:
        confidence = "high"
    elif data_years >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    warnings: list[str] = []
    if is_cycle and not r1_hit:
        warnings.append("仅凭量化特征判定周期（行业清单未命中），建议人工复核")
    if is_cycle and has_dividend:
        warnings.append("周期股分红随周期波动，红利持续性需结合周期位置评估")
    for r in div_rules:
        if r.verdict is Verdict.HIT and "警戒" in r.detail:
            warnings.append(r.detail)
    listed_years = _listed_years(basic.list_date)
    if has_dividend and listed_years is not None and listed_years < 5:
        warnings.append("上市不足 5 年，连续性按上市以来口径判定")
    if data_years < 3 and style != "OUT_OF_SCOPE":
        warnings.append(f"有效财务数据仅 {data_years} 年，风格判定置信度低")

    modules: list[str] = []
    if is_cycle:
        modules.append("cycle_position")
    if has_dividend:
        modules.append("dividend_sustainability")
    if style == "growth":
        modules.append("growth_quality_check")
    if tag == "consumer" or style in ("value", "balanced"):
        modules.append("moat_check")

    output = ClassificationOutput(
        symbol=basic.symbol, name=basic.name, style=style,
        badges=("dividend",) if has_dividend else (),
        industry_tag=tag, confidence=confidence,
        signals=signals, warnings=tuple(warnings),
        evidence={
            "data": {"years_available": data_years, "industry": basic.industry},
            "cycle": {"R1_industry": "HIT" if r1_hit else "MISS",
                      "R2_quant": {"verdict": r2.verdict.value, "detail": r2.detail}},
            "dividend": [{"rule": r.rule, "verdict": r.verdict.value, "detail": r.detail}
                         for r in div_rules],
            "style": style_evidence,
            "industry_tag": {"industry": basic.industry, "tag": tag},
        },
        weight_table=style if style != "OUT_OF_SCOPE" else "",
        modules=tuple(modules),
    )
    return asdict(output)


# ---------- 度量辅助（纯函数） ----------

def _valuation_percentiles(daily: list[dict]) -> tuple[float | None, float | None]:
    """当前 PE/PB 在近 5 年序列中的分位；最新交易日缺值（亏损）返回 None。"""
    def pctl(field: str) -> float | None:
        series = [r.get(field) for r in daily]
        if not series or series[-1] is None:
            return None
        current = series[-1]
        values = [v for v in series if v is not None]
        return sum(1 for v in values if v <= current) / len(values)

    return pctl("pe_ttm"), pctl("pb")


def _year_end_dv_ratios(daily: list[dict], this_year: int, n: int) -> list[float]:
    """最近 n 个完整自然年年末的股息率（%）。"""
    by_year: dict[int, dict] = {}
    for r in daily:
        td = str(r.get("trade_date") or "")
        if len(td) >= 4:
            by_year[int(td[:4])] = r  # 已按日期升序，后写覆盖 = 年末最后一行
    complete = sorted(y for y in by_year if y < this_year)[-n:]
    return [by_year[y]["dv_ratio"] for y in complete if by_year[y].get("dv_ratio") is not None]


def _consecutive_div_years(divs: dict[int, float], this_year: int) -> int | None:
    """从最近完整财年往回数的连续现金分红年数。无记录返回 None。

    不从当年起算：进行中年度可能只有未实施的预案行（金额 0），
    会误伤真实连续分红的公司（金标准教训：长电 2026 中期预案未实施）。
    """
    if not divs:
        return None
    streak, year = 0, this_year - 1
    while divs.get(year, 0.0) > 0:
        streak += 1
        year -= 1
    return streak


def _cum_div_and_fcf(divs: dict[int, float], daily: list[dict],
                     cashflow: dict[int, dict], indicators: dict[int, dict],
                     this_year: int, n: int) -> tuple[float | None, float | None]:
    """近 n 个完整财年：累计分红金额（元）与累计 FCF（元）。

    分红金额 = 每股分红 × 年末总股本(万股→×1e4)；FCF 优先现金流量表口径，
    缺失时回退 fina_indicator 的 fcff（长电 2025 现金流量表字段为空的金标准教训）。
    任一年份两侧都缺数据则对应侧返回 None（D4 降级 SKIP，不硬造）。
    """
    by_year: dict[int, dict] = {}
    for r in daily:
        td = str(r.get("trade_date") or "")
        if len(td) >= 4:
            by_year[int(td[:4])] = r

    years = list(range(this_year - 1, this_year - 1 - n, -1))
    div_amounts, fcfs = [], []
    for year in years:
        ps = divs.get(year)
        share = by_year.get(year, {}).get("total_share")
        if ps is None or share is None:
            return None, None
        div_amounts.append(ps * share * 1e4)
        fcf = cashflow.get(year, {}).get("fcf")
        if fcf is None:
            fcf = indicators.get(year, {}).get("fcff")
        if fcf is None:
            return None, None
        fcfs.append(fcf)
    return sum(div_amounts), sum(fcfs)


def _listed_years(list_date: str) -> int | None:
    if not list_date or len(list_date) != 8:
        return None
    try:
        ld = datetime.strptime(list_date, "%Y%m%d")
    except ValueError:
        return None
    return (datetime.now() - ld).days // 365


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "NA"
