"""周期位置判定（04 号文档 §1）：产能周期代理信号 → 位置判定 → 评分修正意见。"""
from __future__ import annotations

from sqa.config import Thresholds
from sqa.data.tushare_client import TushareClient
from sqa.minesweeper.fetch import fetch_annual_full
from sqa.modules import common as C

# 折旧字段（tushare 真实字段：depr_fa_coga_dpba；集中管理，避免版本兼容猜测）
_DEPRECIATION_FIELDS = ("depr_fa_coga_dpba",)

_CHECKLIST = [
    {"id": "product_price_pctl", "question": "主营产品价格处于近10年什么分位",
     "status": "pending", "method": "行业价格数据库（百川/卓创）或期货主力合约"},
    {"id": "industry_capacity_utilization", "question": "行业产能利用率与库存绝对水位",
     "status": "pending", "method": "行业协会数据 / 工业portfolio库存指数"},
    {"id": "supply_schedule", "question": "未来 2 年行业新增产能投放计划",
     "status": "pending", "method": "公司公告资本开支 + 行业扩产新闻"},
]

_ADVICE = {
    "bottom": "盈利分受周期压制属预期内；健康/现金流权重已最高，若两维仍高分则为优质周期底（神华型）",
    "peak": "盈利能力分含景气溢价，跨周期评估需打折；警惕高 CAPEX 埋下下一轮过剩",
    "recovery": "左侧确认中，关注库存去化与提价持续性",
    "downturn": "景气自高位回落，规避盈利下修风险",
}
_ADVICE_CN = {"bottom": "底部区域", "peak": "景气高位", "recovery": "复苏上行", "downturn": "景气回落"}


def classify_position(profit_pctl: float | None, profit_yoy: float | None) -> str | None:
    """位置判定（04 号文档 §1.2）。分位缺失时退化为纯方向判断。"""
    if profit_pctl is None and profit_yoy is None:
        return None
    if profit_pctl is not None:
        if profit_pctl <= 0.30:
            return "bottom"
        if profit_pctl >= 0.70:
            return "peak"
    if profit_yoy is not None and profit_yoy > 0:
        return "recovery"
    return "downturn"


def capex_reading(ratio: float | None) -> str:
    if ratio is None:
        return "折旧字段缺失，SKIP"
    if ratio < 1.5:
        return "底部/出清区（<1.5，肖璟阈值）"
    if ratio > 2.5:
        return "产能激增（>2.5）"
    return "中性（1.5-2.5）"


def run(ts_code: str, style: str, client: TushareClient,
        cfg: Thresholds, refresh: bool = False) -> dict:
    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years, refresh)
    income = fetch_annual_full(client, "income", ts_code, cfg.history_years, refresh)
    balance = fetch_annual_full(client, "balancesheet", ts_code, cfg.history_years, refresh)
    cashflow = fetch_annual_full(client, "cashflow", ts_code, cfg.history_years, refresh)

    years = sorted(set(ind) | set(income) | set(balance) | set(cashflow))
    profit = C.series(income, "n_income_attr_p", years)
    gm = C.series(ind, "grossprofit_margin", years)
    inv_turn = C.series(ind, "inv_turn", years)
    cip = C.series(balance, "cip", years)

    profit_pctl = C.pctl_latest(profit)
    margin_pctl = C.pctl_latest(gm)
    profit_yoy = C.yoy_latest(profit)
    position = classify_position(profit_pctl, profit_yoy)

    latest_cf = cashflow.get(years[-1], {}) if years else {}
    capex = latest_cf.get("c_pay_acq_const_fiolta")
    dep = next((latest_cf.get(f) for f in _DEPRECIATION_FIELDS
                if latest_cf.get(f) is not None), None)
    capex_ratio = capex / dep if capex is not None and dep else None

    inv_vals = C.clean(inv_turn)
    inv_change = inv_vals[-1] / inv_vals[-2] - 1.0 if len(inv_vals) >= 2 and inv_vals[-2] > 0 else None
    cip_vals = C.clean(cip)
    cip_yoy = cip_vals[-1] / cip_vals[-2] - 1.0 if len(cip_vals) >= 2 and cip_vals[-2] > 0 else None

    agree = None
    if profit_pctl is not None and margin_pctl is not None:
        both_low = profit_pctl < 0.35 and margin_pctl < 0.35
        both_high = profit_pctl > 0.65 and margin_pctl > 0.65
        agree = both_low or both_high
    confidence = "high" if agree else "medium"

    return {
        "module": "cycle_position", "triggered_by": "style=cyclical",
        "position": position,
        "position_cn": _ADVICE_CN.get(position or "", "NA"),
        "confidence": confidence,
        "signals": {
            "capex_to_depreciation": {
                "value": round(capex_ratio, 2) if capex_ratio else None,
                "reading": capex_reading(capex_ratio)},
            "profit_percentile": {"value": profit_pctl,
                                  "reading": "10年净利分位 " + C.pct(profit_pctl)},
            "margin_percentile": {"value": margin_pctl,
                                  "reading": "10年毛利率分位 " + C.pct(margin_pctl)},
            "profit_yoy": {"value": profit_yoy,
                           "reading": "最新净利同比 " + C.pct(profit_yoy)},
            "inventory_turnover_change": {
                "value": inv_change, "reading": (
                    "去库存完成（周转改善）" if inv_change and inv_change > 0.1
                    else "库存仍在积压" if inv_change and inv_change < -0.1
                    else "库存周转平稳")},
            "cip_yoy": {"value": cip_yoy,
                        "reading": "在建工程同比 " + C.pct(cip_yoy)},
        },
        "checklist": _CHECKLIST,
        "quality_advice": _ADVICE.get(position or "", "数据不足，无法给出周期位置意见"),
    }
