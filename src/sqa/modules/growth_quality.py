"""成长质量校验（04 号文档 §3）：静水正向三看 + 反面证法的量化与清单化。"""
from __future__ import annotations

from sqa.config import Thresholds
from sqa.data.tushare_client import TushareClient
from sqa.minesweeper.fetch import fetch_annual_full
from sqa.modules import common as C
from sqa.quality.anchors import score_anchor
from sqa.quality.metrics import cagr_last_n

# 静水 super-growth-quality-checklist 蒸馏（两路径 + 反面证法）
_CHECKLIST = [
    {"path": "正向·长线价投视角", "id": "monopoly", "question": "垄断性：是否独占某种生产要素/关系？",
     "status": "pending"},
    {"path": "正向·长线价投视角", "id": "pricing_power", "question": "定价权：能否自主提价而不流失客户？",
     "status": "pending"},
    {"path": "正向·长线价投视角", "id": "durability", "question": "持续性：这种优势能维持多久？",
     "status": "pending"},
    {"path": "正向·景气价投视角", "id": "macro_stability", "question": "宏观经济环境是否支持？",
     "status": "pending"},
    {"path": "正向·景气价投视角", "id": "industry_ceiling", "question": "行业天花板：渗透率与市场空间还剩多少？",
     "status": "pending"},
    {"path": "正向·景气价投视角", "id": "growth_certainty", "question": "业绩增速的确定性来自哪里（订单/产能/复购）？",
     "status": "pending"},
    {"path": "反面证法", "id": "rev_slowing", "question": "业绩增速与增量是否持续下滑？",
     "status": "pending"},
    {"path": "反面证法", "id": "price_new_low", "question": "股价是否持续创新低（剔除大盘因素）？",
     "status": "pending"},
    {"path": "反面证法", "id": "product_worsening", "question": "产品/服务竞争力是否持续变差？",
     "status": "pending"},
    {"path": "反面证法", "id": "structural_decline", "question": "所处行业是否结构性走下坡（天花板萎缩）？"
     "注意：与周期性低谷区分（周期股已在分类层跳出本模块）",
     "status": "pending"},
]


def volatility_score(rev_yoy_std: float | None) -> float | None:
    """G-vol：增速标准差 → 分（越稳越真）。"""
    if rev_yoy_std is None:
        return None
    return score_anchor(rev_yoy_std, 0.30, 0.15, 0.05)


def verdict_of(score: float | None) -> str:
    if score is None:
        return "insufficient_data"
    if score >= 75:
        return "solid"
    return "acceptable" if score >= 45 else "poor"


def run(ts_code: str, style: str, client: TushareClient,
        cfg: Thresholds, refresh: bool = False) -> dict:
    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years, refresh)
    income = fetch_annual_full(client, "income", ts_code, cfg.history_years, refresh)
    balance = fetch_annual_full(client, "balancesheet", ts_code, cfg.history_years, refresh)

    years = sorted(set(ind) | set(income) | set(balance))
    revenue = C.series(income, "revenue", years)
    ar = C.series(balance, "accounts_receiv", years)
    profit_dedt = C.series(ind, "profit_dedt", years)

    # 近5年营收增速的标准差
    rev_vals = C.clean(revenue)
    yoys = [rev_vals[i] / rev_vals[i - 1] - 1.0
            for i in range(max(1, len(rev_vals) - 5), len(rev_vals))
            if rev_vals[i - 1] > 0]
    vol_std = C.std(yoys)

    rev_cagr = cagr_last_n(revenue, 3)
    np_cagr = cagr_last_n(profit_dedt, 3)
    sync_pp = (np_cagr - rev_cagr) * 100 if np_cagr is not None and rev_cagr is not None else None

    rev_yoy = C.yoy_latest(revenue)
    ar_yoy = C.yoy_latest(ar)
    trend_gap = (rev_yoy - rev_cagr) * 100 if rev_yoy is not None and rev_cagr is not None else None
    quality_gap = (ar_yoy - rev_yoy) * 100 if ar_yoy is not None and rev_yoy is not None else None

    parts = {
        "growth_volatility": (vol_std, volatility_score(vol_std),
                              "近5年营收增速标准差"),
        "profit_sync": (sync_pp, score_anchor(sync_pp, -15, 5, 15) if sync_pp is not None else None,
                        "扣非CAGR−营收CAGR (pp)"),
        "trend_gap": (trend_gap, score_anchor(trend_gap, -15, 0, 10) if trend_gap is not None else None,
                      "最新同比−3年CAGR (pp)"),
        "income_quality": (quality_gap, score_anchor(quality_gap, 20, 0, -5) if quality_gap is not None else None,
                           "应收增速−营收增速 (pp)"),
    }
    scores = [s for _, s, _ in parts.values() if s is not None]
    overall = round(sum(scores) / len(scores), 1) if scores else None

    notes: list[str] = []
    if sync_pp is not None and sync_pp > 30:
        notes.append("扣非增速大幅快于营收（>+30pp），核查一次性收益/减值转回")
    if verdict_of(overall) == "poor":
        notes.append("增长真实性存疑：波动大/趋势向下/含金量差至少两项命中")

    return {
        "module": "growth_quality_check", "triggered_by": "style=growth",
        "score": overall,
        "verdict": verdict_of(overall),
        "signals": {k: {"value": (round(v, 4) if v is not None else None),
                        "score": s, "desc": d} for k, (v, s, d) in parts.items()},
        "notes": notes,
        "checklist": _CHECKLIST,
        "checklist_note": "静水 super-growth-quality-checklist 蒸馏；正向三看按流派二选一核对，反面证法任一命中即警示",
    }
