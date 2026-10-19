"""护城河检查（04 号文档 §4）：肖璟护城河五类型的量化代理 + 识别清单。"""
from __future__ import annotations

from sqa.config import Thresholds
from sqa.data.fetchers import fetch_basic
from sqa.data.tushare_client import TushareClient
from sqa.minesweeper.fetch import fetch_annual_full
from sqa.modules import common as C
from sqa.quality.anchors import score_anchor
from sqa.quality.fetch import fetch_peer_medians
from sqa.quality.metrics import median

# 肖璟《如何快速了解一个行业》第4章：护城河五类型识别问题
_CHECKLIST = [
    {"type": "资源垄断", "id": "resource", "question": "是否独占某种生产要素（矿权/牌照/区位/频率）？",
     "status": "pending"},
    {"type": "网络效应", "id": "network", "question": "用户增加是否让产品对每个用户更有价值？",
     "status": "pending"},
    {"type": "转换成本", "id": "switching", "question": "客户更换供应商的成本有多高（数据/流程/学习曲线）？",
     "status": "pending"},
    {"type": "规模经济", "id": "scale", "question": "单位成本是否随规模显著下降？对手砸钱能否绕开？",
     "status": "pending"},
    {"type": "品牌溢价", "id": "brand", "question": "同样的东西能否卖得更贵且依然畅销？",
     "status": "pending"},
]


def erosion_warning(gm_trend_pp: float | None, gm_vs_peer_pp: float | None) -> str | None:
    if gm_trend_pp is not None and gm_vs_peer_pp is not None \
            and gm_trend_pp < -5 and gm_vs_peer_pp < 0:
        return (f"护城河侵蚀警示：毛利率10年下滑 {abs(gm_trend_pp):.1f}pp 且已低于同行 "
                f"{abs(gm_vs_peer_pp):.1f}pp")
    return None


def run(ts_code: str, style: str, client: TushareClient,
        cfg: Thresholds, refresh: bool = False) -> dict:
    basic = fetch_basic(client, ts_code, refresh)
    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years, refresh)
    peers = fetch_peer_medians(client, basic.industry, refresh)

    years = sorted(ind)
    gm = C.series(ind, "grossprofit_margin", years)
    roic = C.series(ind, "roic", years)

    gm_vals = C.clean(gm)
    gm_latest = gm_vals[-1] if gm_vals else None
    peer_gm = (peers or {}).get("gm_median")
    rel_pp = gm_latest - peer_gm if gm_latest is not None and peer_gm is not None else None
    trend_pp = gm_vals[-1] - gm_vals[0] if len(gm_vals) >= 5 else None
    roic_med = median(roic[-10:])
    roic_std = C.std(roic[-10:])

    parts = {
        "gm_vs_peers": (rel_pp, score_anchor(rel_pp, -15, 0, 15) if rel_pp is not None else None,
                        "最新毛利率−同行中位数 (pp)"),
        "roic_median": (roic_med, score_anchor(roic_med, 3, 7, 15) if roic_med is not None else None,
                        "10年ROIC中位数 (%)"),
        "roic_stability": (roic_std, score_anchor(roic_std, 10, 5, 2) if roic_std is not None else None,
                           "10年ROIC标准差（越小越稳）"),
        "gm_trend": (trend_pp, score_anchor(trend_pp, -15, 0, 10) if trend_pp is not None else None,
                     "毛利率10年首末变化 (pp)"),
    }
    scores = [s for _, s, _ in parts.values() if s is not None]
    overall = round(sum(scores) / len(scores), 1) if scores else None

    return {
        "module": "moat_check",
        "triggered_by": f"industry={basic.industry} style={style}",
        "score": overall,
        "verdict": ("strong" if overall is not None and overall >= 75
                    else "moderate" if overall is not None and overall >= 45
                    else "weak" if overall is not None else "insufficient_data"),
        "signals": {k: {"value": (round(v, 4) if v is not None else None),
                        "score": s, "desc": d} for k, (v, s, d) in parts.items()},
        "erosion_warning": erosion_warning(trend_pp, rel_pp),
        "peer_sample": (peers or {}).get("n_peers"),
        "checklist": _CHECKLIST,
        "checklist_note": "肖璟护城河五类型；注意：导入期行业谈护城河为时过早，衰退期护城河正在瓦解",
    }
