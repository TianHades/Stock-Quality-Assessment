"""估值引擎（05 号文档）：按主风格选方法 → verdict + 信号 + 附注。

纯函数判定逻辑与取数编排分离：_*_verdict 系列可离线单测。
"""
from __future__ import annotations

import os

from sqa.config import Thresholds, load_thresholds, risk_free_rate
from sqa.data.fetchers import fetch_annual_indicator, fetch_basic, fetch_daily_basic, resolve_ts_code
from sqa.data.tushare_client import TushareClient
from sqa.quality.metrics import cagr_last_n

# 周期股强制附注（05 号文档 §1.3）
_CYC_NO_PE_NOTE = "周期股禁用 PE——景气顶部低 PE 是周期见顶信号（盈利即将回落），不是便宜"


def _pctl_current(values: list[float | None]) -> float | None:
    """当前值（最新交易日）在序列中的分位；最新日为 None 时返回 None。

    不能用"最后一个非空值"冒充当前值：最新 PE 为 None 通常表示当期亏损，
    应触发 PEG/PE 降级，而不是拿几个月前的旧 PE 继续算分位。
    """
    if not values or values[-1] is None:
        return None
    current = values[-1]
    series_ = [v for v in values if v is not None]
    return sum(1 for v in series_ if v <= current) / len(series_)


def percentile_verdict(pctl: float | None) -> str:
    """分位法判定（05 号文档 §1.1）。"""
    if pctl is None:
        return "NA"
    if pctl < 0.30:
        return "undervalued"
    if pctl > 0.70:
        return "overvalued"
    return "fair"


def peg_of(pe: float | None, np_cagr: float | None) -> float | None:
    """PEG = PE ÷ (扣非 CAGR × 100)。亏损/低增速/数据不足返回 None。"""
    if pe is None or pe <= 0 or np_cagr is None or np_cagr <= 0:
        return None
    return pe / (np_cagr * 100)


def growth_verdict(peg: float | None, ps_pctl: float | None) -> tuple[str, list[str]]:
    """成长股：PEG 主判，降级 PS 分位（05 号文档 §1.2）。"""
    if peg is None:
        return percentile_verdict(ps_pctl), ["PEG 不适用（亏损或增速数据不足），降级 PS 分位"]
    if peg < 1.0:
        return "undervalued", []
    if peg <= 2.0:
        return "fair", []
    notes = []
    if peg > 3.0:
        notes.append(f"PEG {peg:.1f}，市场给足了成长定价，透支风险")
    return "overvalued", notes


def cyclical_verdict(pb_pctl: float | None,
                     position: str | None) -> tuple[str, list[str]]:
    """周期股双验证矩阵（05 号文档 §1.3）。"""
    if pb_pctl is None:
        return "NA", ["PB 分位缺失"]
    low, high = pb_pctl < 0.40, pb_pctl > 0.60

    if position is None:
        return percentile_verdict(pb_pctl), ["周期位置缺失，仅 PB 单腿判断", _CYC_NO_PE_NOTE]
    if position == "bottom":
        if low:
            return "undervalued", ["PB 低位 + 周期底部：双重确认的低估区", _CYC_NO_PE_NOTE]
        return "fair", ["周期底部但 PB 不低：市场已抢跑或资产质量疑虑", _CYC_NO_PE_NOTE]
    if position == "recovery":
        if low:
            return "undervalued", ["PB 低位 + 复苏：左侧机会区", _CYC_NO_PE_NOTE]
        return "fair", [_CYC_NO_PE_NOTE]
    if position == "peak":
        if high:
            return "overvalued", ["PB 高位 + 景气顶部：双重高估", _CYC_NO_PE_NOTE]
        return "fair", ["景气顶部估值中性：盈利回落风险大于估值风险", _CYC_NO_PE_NOTE]
    # downturn
    if high:
        return "overvalued", ["PB 高位 + 景气回落：戴维斯双杀风险", _CYC_NO_PE_NOTE]
    notes = [_CYC_NO_PE_NOTE]
    if low:
        notes.append("低 PB + 景气下行：左左侧，低 PB 可能越接越低")
    return "fair", notes


def dividend_spread(dv_ratio: float | None, rf_pct: float) -> float | None:
    """股息率利差（pp）。"""
    if dv_ratio is None:
        return None
    return dv_ratio - rf_pct


def dividend_verdict(spread_pp: float | None, dv_pctl: float | None) -> tuple[str | None, str]:
    """红利叠加：返回 (信号, 说明)。信号不覆盖主风格 verdict。"""
    if spread_pp is None:
        return None, "股息率数据缺失"
    if spread_pp >= 2.0 and dv_pctl is not None and dv_pctl >= 0.60:
        return "dividend_attractive", f"股息率利差 {spread_pp:.1f}pp（分位 {dv_pctl:.0%}），类债逻辑成立"
    if spread_pp < 1.0:
        return "dividend_weak", f"股息率利差仅 {spread_pp:.1f}pp，类债逻辑弱化（机会成本高）"
    return None, f"股息率利差 {spread_pp:.1f}pp，中性"


def run_valuation(symbol: str, style: str, badges: tuple | list = (),
                  cycle_position: str | None = None,
                  cfg: Thresholds | None = None, client: TushareClient | None = None,
                  refresh: bool = False) -> dict:
    """主入口：输出 05 号文档 §2 的 JSON 结构。"""
    cfg = cfg or load_thresholds()
    if client is None:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))

    ts_code = resolve_ts_code(symbol)
    basic = fetch_basic(client, ts_code, refresh)
    daily = fetch_daily_basic(client, ts_code, cfg.val_pctl_window_years, refresh)

    def daily_field(field: str) -> list[float | None]:
        return [r.get(field) for r in daily]

    pe = daily_field("pe_ttm")
    pb = daily_field("pb")
    ps = daily_field("ps_ttm")
    dv = daily_field("dv_ratio")

    pe_pctl = _pctl_current(pe)
    pb_pctl = _pctl_current(pb)
    ps_pctl = _pctl_current(ps)
    dv_pctl = _pctl_current(dv)
    pb_now = pb[-1] if pb and pb[-1] is not None else None
    pe_now = pe[-1] if pe and pe[-1] is not None else None
    dv_now = dv[-1] if dv and dv[-1] is not None else None

    metrics: dict = {"pe_pctl": pe_pctl, "pb_pctl": pb_pctl, "ps_pctl": ps_pctl,
                     "dv_pctl": dv_pctl, "pe_now": pe_now, "pb_now": pb_now,
                     "dv_now": dv_now}
    signals: list[str] = []
    methods: list[str] = []
    notes: list[str] = []

    if style == "value":
        verdict = percentile_verdict(pb_pctl)
        methods.append("pb_percentile")
        if pb_pctl is not None and pb_now is not None and pb_now < 1.0 and pb_pctl < 0.20:
            signals.append("深度价值（PB<1 且分位<20%）")
    elif style == "balanced":
        verdict = percentile_verdict(pe_pctl)
        methods.append("pe_percentile")
        if verdict == "NA" and pb_pctl is not None:
            verdict = percentile_verdict(pb_pctl)
            notes.append("PE 分位缺失（亏损等），降级 PB")
    elif style == "growth":
        ind = fetch_annual_indicator(client, ts_code, cfg.history_years, refresh)
        years = sorted(ind)
        np_cagr = cagr_last_n([ind[y].get("profit_dedt") for y in years], 3)
        peg = peg_of(pe_now, np_cagr)
        metrics["peg"] = round(peg, 2) if peg is not None else None
        metrics["np_cagr"] = np_cagr
        verdict, peg_notes = growth_verdict(peg, ps_pctl)
        methods.append("peg" if peg is not None else "ps_percentile")
        notes.extend(peg_notes)
    elif style == "cyclical":
        verdict, cyc_notes = cyclical_verdict(pb_pctl, cycle_position)
        methods.append("pb_percentile+cycle_double_check")
        notes.extend(cyc_notes)
        metrics["cycle_position"] = cycle_position
    else:
        verdict, methods = "NA", []

    # 红利叠加（不覆盖主 verdict）
    if "dividend" in badges:
        rf_pct = risk_free_rate(client) * 100
        spread = dividend_spread(dv_now, rf_pct)
        metrics["div_spread_pp"] = round(spread, 2) if spread is not None else None
        metrics["rf_pct"] = round(rf_pct, 2)
        signal, desc = dividend_verdict(spread, dv_pctl)
        methods.append("dividend_spread")
        if signal:
            signals.append(signal)
        notes.append(desc)

    return {
        "symbol": basic.symbol, "name": basic.name,
        "module": "valuation", "style": style,
        "methods_applied": methods,
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in metrics.items()},
        "verdict": verdict,
        "signals": signals,
        "notes": notes,
    }
