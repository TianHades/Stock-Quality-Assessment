"""质量评分引擎（03 号文档）：六维度 × 22 指标 → 加权总分 → 评级。"""
from __future__ import annotations

import os
from datetime import datetime

from sqa.config import Thresholds, load_thresholds
from sqa.data.fetchers import (
    compute_fcf, fetch_basic, fetch_dividend_records, resolve_ts_code,
)
from sqa.data.tushare_client import TushareClient
from sqa.minesweeper.fetch import (
    fetch_annual_full, fetch_holder_series, fetch_pledge_ratio,
    holder_ratio_series,
)
from sqa.quality import metrics as M
from sqa.quality.anchors import score_anchor
from sqa.quality.fetch import fetch_peer_medians
from sqa.quality.weights import DIMENSIONS, resolve_weights

_GRADE_BANDS: tuple[tuple[str, float], ...] = (
    ("A+", 85.0), ("A", 75.0), ("B", 60.0), ("C", 45.0), ("D", 0.0),
)
_GRADE_ORDER = [g for g, _ in _GRADE_BANDS]


def map_grade(score: float) -> str:
    for grade, floor in _GRADE_BANDS:
        if score >= floor:
            return grade
    return "D"


def adjust_grade(grade: str, ms_verdict: str) -> str:
    """排雷联动（03 号文档 §4.4）：中风险降一档，强制分析的高风险双降。"""
    if ms_verdict not in ("degraded", "blocked"):
        return grade
    idx = _GRADE_ORDER.index(grade)
    step = 2 if ms_verdict == "blocked" else 1
    return _GRADE_ORDER[min(len(_GRADE_ORDER) - 1, idx + step)]


def _ind(name: str, value: float | None, anchors: tuple[float, float, float],
         window: str, note: str = "", structural: bool = False) -> dict:
    """单指标条目：值缺失 → SKIP；锚点全 0 视为相对锚点缺基准 → SKIP。

    structural=True 标记"结构性不可用"（如同行管道未建），不参与维度的
    缺失多数判定（否则永久性缺一项会让整维度频繁 SKIP）。
    """
    value = M.finite_or_none(value)
    if value is None or (anchors[0] == anchors[1] == anchors[2] == 0):
        return {"name": name, "value": None, "score": None, "window": window,
                "note": note or "数据缺失", "structural": structural}
    zero, fifty, hundred = anchors
    return {
        "name": name, "value": round(value, 4),
        "score": round(score_anchor(value, zero, fifty, hundred), 1),
        "window": window, "note": note, "structural": structural,
    }


def run_quality(symbol: str, style: str, badges: tuple | list = (),
                ms_verdict: str = "pass", cfg: Thresholds | None = None,
                client: TushareClient | None = None,
                refresh: bool = False) -> dict:
    """主入口：输出 03 号文档 §5 的 JSON 结构。"""
    cfg = cfg or load_thresholds()
    if style not in ("growth", "value", "cyclical", "balanced"):
        raise ValueError(f"质量评分不支持主风格: {style}")
    if client is None:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))

    ts_code = resolve_ts_code(symbol)
    basic = fetch_basic(client, ts_code, refresh)
    this_year = datetime.now().year
    is_cycle = style == "cyclical"

    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years, refresh)
    income = fetch_annual_full(client, "income", ts_code, cfg.history_years, refresh)
    balance = fetch_annual_full(client, "balancesheet", ts_code, cfg.history_years, refresh)
    cashflow = fetch_annual_full(client, "cashflow", ts_code, cfg.history_years, refresh)
    divs = fetch_dividend_records(client, ts_code, refresh)
    pledge = fetch_pledge_ratio(client, ts_code, refresh)
    holders = holder_ratio_series(fetch_holder_series(client, ts_code, refresh=refresh))
    peers = fetch_peer_medians(client, basic.industry, refresh, exclude_code=ts_code)

    years = sorted(set(ind) | set(income) | set(balance) | set(cashflow))

    def series(source: dict, field: str) -> list[float | None]:
        return [source.get(y, {}).get(field) for y in years]

    revenue = series(income, "revenue")
    np_ = series(income, "n_income_attr_p")
    ar = series(balance, "accounts_receiv")
    ta = series(balance, "total_assets")
    ocf = series(cashflow, "n_cashflow_act")
    cash_recv = series(cashflow, "c_fr_sale_sg")

    def fcf_at(i: int) -> float | None:
        return compute_fcf(cashflow.get(years[i]), ind.get(years[i]))

    fcf = [fcf_at(i) for i in range(len(years))]

    dims: dict[str, dict] = {}
    skip_dims: set[str] = set()

    # ---- 1. 盈利能力（周期股 10 年中位数 / 其他 3 年均值）----
    pwin = "10y_median" if is_cycle else "3y_mean"
    agg = M.median if is_cycle else M.mean
    pwindow = 10 if is_cycle else 3

    def agg_last(field: str) -> float | None:
        return agg(series(ind, field)[-pwindow:])

    # 净利率口径（03 §1.1）：归母净利/营收，不能用 fina_indicator.netprofit_margin
    # （实测=总净利/营收，含少数股东损益；2026-10-25 复核修正）。
    net_margins = [
        (a / b * 100.0) if (a is not None and b not in (None, 0)) else None
        for a, b in zip(np_, revenue)
    ]
    net_margin = agg(net_margins[-pwindow:])

    gm_peer = (peers or {}).get("gm_median")
    dims["profitability"] = {"indicators": [
        _ind("roe", agg_last("roe"), (3, 8, 18), pwin),
        _ind("roic", agg_last("roic"), (3, 7, 15), pwin),
        _ind("gross_margin", agg_last("grossprofit_margin"),
             (gm_peer - 15, gm_peer, gm_peer + 15) if gm_peer is not None else (0, 0, 0),
             pwin, "" if gm_peer is not None else "同行毛利率中位数缺失（相对锚点不可用）"),
        _ind("net_margin", net_margin, (2, 6, 15), pwin),
    ]}

    # ---- 2. 成长性（周期股整维 SKIP——03 号文档 §4.2）----
    if is_cycle:
        skip_dims.add("growth")
        dims["growth"] = {"indicators": [], "score": None, "weight": 0.0,
                          "note": "cyclical_skip（03 号文档 §4.2）"}
    else:
        rev_cagr = M.cagr_last_n(revenue, 3)
        np_cagr = M.cagr_last_n(series(ind, "profit_dedt"), 3)
        rev_yoy = M.yoy(revenue[-1], revenue[-2])
        ar_yoy = M.yoy(ar[-1], ar[-2])
        trend_gap = (rev_yoy - rev_cagr) * 100 if rev_yoy is not None and rev_cagr is not None else None
        quality_gap = (ar_yoy - rev_yoy) * 100 if ar_yoy is not None and rev_yoy is not None else None
        dims["growth"] = {"indicators": [
            _ind("rev_cagr", rev_cagr * 100 if rev_cagr is not None else None, (0, 8, 25), "3y_cagr"),
            _ind("np_dedt_cagr", np_cagr * 100 if np_cagr is not None else None, (0, 10, 30), "3y_cagr"),
            _ind("trend_gap", trend_gap, (-15, 0, 10), "latest_yoy_minus_cagr"),
            _ind("income_quality", quality_gap, (20, 0, -5), "ar_growth_minus_rev_growth"),
        ]}

    # ---- 3. 现金流质量（周期股 10 年累计 / 其他 3 年累计）----
    cwin = "10y_cum" if is_cycle else "3y_cum"
    tail = 10 if is_cycle else 3
    fcf_ratio = M.cumulative_ratio(fcf[-tail:], revenue[-tail:])
    dims["cashflow"] = {"indicators": [
        _ind("ocf_to_np", M.cumulative_ratio(ocf[-tail:], np_[-tail:]), (0.5, 0.9, 1.2), cwin),
        _ind("cash_to_rev", M.cumulative_ratio(cash_recv[-tail:], revenue[-tail:]), (0.8, 0.95, 1.1), cwin),
        _ind("fcf_strength", fcf_ratio * 100 if fcf_ratio is not None else None, (0, 3, 8), cwin),
    ]}

    # ---- 4. 财务健康（最新年度；资产负债率相对同行中位数）----
    latest_ind = ind.get(years[-1], {}) if years else {}
    latest_bal = balance.get(years[-1], {}) if years else {}
    latest_inc = income.get(years[-1], {}) if years else {}
    debt_peer = (peers or {}).get("debt_median")

    ta = latest_bal.get("total_assets")
    ncl = next((latest_bal.get(n) for n in ("non_cur_liab_due_1y", "nca_within_1y_liab",
                                           "ncl_1y", "non_cur_liab_1y")
                if latest_bal.get(n) is not None), 0.0)
    # 有息负债须含一年内到期非流动负债（2026-10-25 复核修正）
    ibd = (sum(float(latest_bal.get(n) or 0.0)
               for n in ("st_borr", "lt_borr", "bond_payable"))
           + float(ncl or 0.0))
    short_debt = float(latest_bal.get("st_borr") or 0.0) + (ncl or 0.0)
    money = latest_bal.get("money_cap")
    # 利息保障：EBIT=(利润总额+利息费用)；分母只用 int_exp（利息费用字段）。
    # 不再用 abs(fin_exp) 净额兜底——净财务费用含利息收入，会算出 60+ 倍假高覆盖；
    # 资本化利息不在 tushare 主表，缺失时 SKIP 并保留人工复核（03 §1.4 口径待深模补数）。
    interest = latest_inc.get("int_exp")
    if interest is not None and interest <= 0:
        interest = None
    total_profit = latest_inc.get("total_profit")
    ebit = (total_profit + interest) if (total_profit is not None
                                         and interest is not None) else None

    dims["health"] = {"indicators": [
        _ind("debt_to_assets", latest_ind.get("debt_to_assets"),
             (debt_peer + 20, debt_peer + 5, debt_peer - 15) if debt_peer is not None else (0, 0, 0),
             "latest", "" if debt_peer is not None else "同行资产负债率中位数缺失"),
        _ind("ibd_ratio", ibd / ta * 100 if ta else None, (40, 20, 5), "latest"),
        _ind("interest_cover",
             ebit / interest if ebit is not None and interest else None,
             (1.5, 4, 10), "latest",
             "" if interest is not None else "利息费用字段缺失/资本化利息未覆盖，SKIP 待人工复核"),
        # 无短期有息负债 = 最佳覆盖（金标准教训：茅台无短债被判 SKIP 拉垮整维度）
        _ind("short_debt_cover",
             money / short_debt if money is not None and short_debt > 0
             else (10.0 if money is not None and short_debt <= 0 else None),
             (0.8, 1.5, 3), "latest", note="" if short_debt > 0 else "无短期有息负债"),
    ]}

    # ---- 5. 运营效率（总资产周转率相对同行；周转天数趋势）----
    ewin = 10 if is_cycle else 5
    assets_turn_latest = next(
        (v for v in reversed(series(ind, "assets_turn")) if v is not None), None)
    if assets_turn_latest is None:
        rev_latest = next((v for v in reversed(revenue) if v is not None), None)
        ta_latest = next((v for v in reversed(ta) if v is not None), None)
        if rev_latest is not None and ta_latest and ta_latest > 0:
            assets_turn_latest = rev_latest / ta_latest
    peer_assets_turn = (peers or {}).get("assets_turn_median")
    assets_anchors = (
        (peer_assets_turn * 0.5, peer_assets_turn, peer_assets_turn * 1.5)
        if peer_assets_turn and peer_assets_turn > 0 else (0, 0, 0)
    )
    dims["efficiency"] = {"indicators": [
        _ind("total_asset_turnover", assets_turn_latest, assets_anchors, "latest",
             "" if peer_assets_turn else "同行总资产周转率中位数缺失（行业样本不足）"),
        _ind("inv_days_change", M.days_change_pct(series(ind, "inv_turn"), ewin),
             (50, 0, -30), f"{ewin}y_days_change"),
        _ind("ar_days_change", M.days_change_pct(series(ind, "ar_turn"), ewin),
             (50, 0, -30), f"{ewin}y_days_change"),
    ]}

    # ---- 6. 治理与股东回报 ----
    eps_map = {y: ind.get(y, {}).get("eps") for y in years}
    payout = M.avg_payout(divs, eps_map, this_year)
    gw = latest_bal.get("goodwill")
    equity = latest_bal.get("total_hldr_eqy_exc_min_int") or latest_bal.get("total_hldr_eqy")
    positive_holders = [h for h in holders if h is not None]
    holder_change = ((positive_holders[-1] - positive_holders[0])
                     if len(positive_holders) >= 2 else None)

    dims["governance"] = {"indicators": [
        _ind("payout_ratio", payout * 100 if payout is not None else None, (0, 20, 40), "3y_mean"),
        _ind("pledge_ratio", pledge, (50, 20, 5), "latest"),
        _ind("holder_change", holder_change, (-5, 0, 1), "3y_span"),
        # 商誉缺失（字段 None）≠ 无商誉：缺失 → SKIP；明确为 0 才给满分
        # （2026-10-25 复核修正：原实现把 None 当 0，神华/茅台等无字段标的被误给满分）
        _ind("goodwill_ratio",
             ((gw / equity * 100) if (gw is not None and equity) else
              (0.0 if gw == 0 else None)),
             (30, 10, 2), "latest",
             "" if gw is not None else "商誉字段缺失"),
    ]}

    # ---- 聚合（03 号文档 §4.3-§4.4）----
    weights, overlay_applied = resolve_weights(
        style, "dividend" in badges, skip_dims)

    total = 0.0
    for dim in DIMENSIONS:
        d = dims[dim]
        scores = [i["score"] for i in d["indicators"] if i["score"] is not None]
        n_all = len([i for i in d["indicators"] if not i.get("structural")])
        n_all = max(n_all, 1)
        d["n_skip"] = sum(1 for i in d["indicators"]
                          if i["score"] is None and not i.get("structural"))
        d.setdefault("weight", weights[dim])
        # 维度 SKIP 条件：缺失严格大于 50%（03 号文档 §4.3；恰好半数可用仍计分）
        if dim in skip_dims or not scores or len(scores) * 2 < n_all:
            d["score"] = None
            d["weight"] = 0.0
            d["note"] = d.get("note") or f"{d['n_skip']}/{n_all} 指标缺失，维度 SKIP"
            continue
        d["score"] = round(sum(scores) / len(scores), 1)
        total += d["score"] * d["weight"] / 100.0

    # 维度 SKIP 后重分配权重（resolve_weights 已处理 skip_dims，但 >50% 缺失的
    # 维度是聚合时才发现的——对这类维度做第二轮重分配）
    newly_skipped = {d for d in DIMENSIONS if dims[d]["score"] is None and d not in skip_dims}
    if newly_skipped:
        weights2, _ = resolve_weights(style, "dividend" in badges, skip_dims | newly_skipped)
        total = 0.0
        for dim in DIMENSIONS:
            d = dims[dim]
            d["weight"] = weights2[dim] if d["score"] is not None else 0.0
            if d["score"] is not None:
                total += d["score"] * d["weight"] / 100.0

    available = sum(
        len([i for i in dims[d]["indicators"] if i["score"] is not None])
        for d in DIMENSIONS
    )
    # 完整度分母用"适用指标数"：剔除设计性 SKIP（周期股 growth）与 structural
    # 永久缺项（总资产周转率），否则周期股天然被压低、易跌破 0.6 不出评级。
    applicable = sum(
        len([i for i in dims[d]["indicators"] if not i.get("structural")])
        for d in DIMENSIONS
    )
    completeness = round(available / applicable, 2) if applicable else 0.0

    quality_score = round(total, 1)
    grade = map_grade(quality_score) if completeness >= 0.6 else None
    grade_adjusted = adjust_grade(grade, ms_verdict) if grade else None

    return {
        "symbol": basic.symbol, "name": basic.name, "style": style,
        "badges": tuple(badges),
        "quality_score": quality_score,
        "grade": grade,
        "grade_adjusted": grade_adjusted,
        "insufficient_data": completeness < 0.6,
        "dimensions": {d: dims[d] for d in DIMENSIONS},
        "adjustments": {
            "dividend_overlay": overlay_applied,
            "minesweeper_risk": ms_verdict,
            "peer_sample": (peers or {}).get("n_peers"),
        },
        "data_completeness": completeness,
    }
