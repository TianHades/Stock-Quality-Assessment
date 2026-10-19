"""分红持续性（04 号文档 §2）：覆盖率/派息率/连续性 → 可持续性判定。"""
from __future__ import annotations

from datetime import datetime

from sqa.config import Thresholds
from sqa.data.fetchers import compute_fcf, fetch_daily_basic, fetch_dividend_records
from sqa.data.tushare_client import TushareClient
from sqa.minesweeper.fetch import fetch_annual_full


def verdict_of(latest_coverage: float | None, latest_payout: float | None,
               payout_peak: float | None, streak: int | None,
               coverage_weakening: bool = False,
               streak_threshold: int = 5) -> tuple[str, list[str]]:
    """可持续性判定（04 号文档 §2.2）。返回 (verdict, 警示列表)。

    2026-10-19 修正：
    - 「走弱」用 coverage_weakening（最新<上年<前年）判断，不再用 min<latest 的反向逻辑；
    - sustainable 必须满足派息率 20%–90%、覆盖≥1.2、峰值≤90%、streak≥阈值；
    - 覆盖/派息数据缺失时返回 watch（不再默认 sustainable）。
    """
    warnings: list[str] = []
    if latest_coverage is None and latest_payout is None:
        return "watch", ["FCF 覆盖与派息率数据均缺失，无法判定分红持续性"]
    if latest_coverage is not None and latest_coverage < 1.0:
        return "at_risk", [f"最新 FCF 对分红覆盖仅 {latest_coverage:.2f}，靠存量现金/融资维持"]
    if latest_payout is not None and latest_payout > 1.0:
        return "at_risk", [f"最新派息率 {latest_payout:.0%}，超过 100%"]

    watch_reasons: list[str] = []
    if latest_coverage is None:
        watch_reasons.append("最新 FCF 覆盖缺失，无法判定分红能力")
    elif latest_coverage < 1.2:
        watch_reasons.append(f"覆盖 {latest_coverage:.2f} 偏薄（<1.2）")
    if coverage_weakening:
        watch_reasons.append("覆盖率连续两年走弱")
    if payout_peak is not None and payout_peak > 0.9:
        watch_reasons.append(f"近5年派息率峰值 {payout_peak:.0%}，波动偏大")
    if latest_payout is None:
        watch_reasons.append("最新派息率缺失")
    elif not 0.20 <= latest_payout <= 0.90:
        watch_reasons.append(
            f"最新派息率 {latest_payout:.0%}，不在可持续区间 [20%, 90%]")
    if watch_reasons:
        return "watch", watch_reasons

    if streak is not None and streak < streak_threshold:
        return "watch", [f"连续分红仅 {streak} 年，政策承诺待验证"]
    return "sustainable", warnings


def run(ts_code: str, style: str, client: TushareClient,
        cfg: Thresholds, refresh: bool = False) -> dict:
    this_year = datetime.now().year
    divs = fetch_dividend_records(client, ts_code, refresh)
    ind = fetch_annual_full(client, "fina_indicator", ts_code, cfg.history_years, refresh)
    cashflow = fetch_annual_full(client, "cashflow", ts_code, cfg.history_years, refresh)
    daily = fetch_daily_basic(client, ts_code, cfg.val_pctl_window_years, refresh)

    def fcf_of(year: int) -> float | None:
        return compute_fcf(cashflow.get(year), ind.get(year))

    share_by_year: dict[int, float] = {}
    for r in daily:
        td = str(r.get("trade_date") or "")
        if len(td) >= 4 and r.get("total_share") is not None:
            share_by_year[int(td[:4])] = float(r["total_share"])  # 万股（年末覆盖）

    coverages: dict[int, float] = {}
    coverage_years = list(range(this_year - 1, this_year - 4, -1))
    for year in coverage_years:
        ps = divs.get(year)
        share = share_by_year.get(year)
        amount = ps * share * 1e4 if ps and share else None
        fcf = fcf_of(year)
        if amount and fcf is not None:
            coverages[year] = fcf / amount

    # 派息率按文档 §2.1 取近 5 年序列（C-payout），与覆盖率的 3 年窗口分开
    payout_years = list(range(this_year - 1, this_year - 6, -1))
    payouts_by_year: dict[int, float] = {}
    for year in payout_years:
        ps = divs.get(year)
        eps = ind.get(year, {}).get("eps")
        if ps and eps and eps > 0:
            payouts_by_year[year] = ps / eps

    streak, y = 0, this_year - 1
    while divs.get(y, 0.0) > 0:
        streak += 1
        y -= 1

    ordered = [coverages.get(y) for y in coverage_years]
    coverage_weakening = (
        len(ordered) >= 3
        and all(v is not None for v in ordered[:3])
        and ordered[0] < ordered[1] < ordered[2]
    )

    latest_coverage = coverages.get(this_year - 1)
    latest_payout = payouts_by_year.get(this_year - 1)
    if latest_payout is None and payouts_by_year:
        latest_payout = payouts_by_year[max(payouts_by_year)]
    payout_peak = max(payouts_by_year.values()) if payouts_by_year else None
    payout_recent = [round(payouts_by_year[y], 3)
                     for y in payout_years if y in payouts_by_year]

    verdict, warns = verdict_of(latest_coverage, latest_payout, payout_peak,
                                streak, coverage_weakening=coverage_weakening)
    if style == "cyclical":
        warns = ["分红随周期波动，景气顶部的高分红不可线性外推"] + warns

    return {
        "module": "dividend_sustainability",
        "triggered_by": "badge=dividend",
        "verdict": verdict,
        "signals": {
            "fcf_coverage_by_year": {str(k): round(v, 2) for k, v in sorted(coverages.items())},
            "payout_recent": payout_recent,
            "payout_peak": round(payout_peak, 3) if payout_peak else None,
            "coverage_weakening": coverage_weakening,
            "consecutive_years": streak,
        },
        "warnings": warns,
    }
