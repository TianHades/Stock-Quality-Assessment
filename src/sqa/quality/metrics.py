"""质量评分的度量计算（纯函数，输入年度序列）。"""
from __future__ import annotations

import math


def median(values: list[float | None]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def cagr(values: list[float | None]) -> float | None:
    """全序列 CAGR：至少 4 个点、基期与终期为正。不足返回 None（SKIP）。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 4 or vals[0] <= 0 or vals[-1] <= 0:
        return None
    return (vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1


def cagr_last_n(values: list[float | None], years_n: int = 3) -> float | None:
    """最近 years_n 个财年的 CAGR（文档口径：近 3 年 CAGR）。

    必须正好取到 years_n+1 个年度点（当前值 + 基期），任一点缺失/非正
    则返回 None（SKIP）——避免用"看起来连续、实际缺年"的序列硬算。
    调用方应传入按财年对齐的序列（含 None 占位）。
    """
    if years_n <= 0:
        return None
    window = list(values[-(years_n + 1):])
    if len(window) < years_n + 1 or any(v is None for v in window):
        return None
    if window[0] <= 0 or window[-1] <= 0:
        return None
    return (window[-1] / window[0]) ** (1 / years_n) - 1


def yoy(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return new / old - 1


def cumulative_ratio(num: list[float | None], den: list[float | None]) -> float | None:
    """累计口径比率：Σ分子/Σ分母（成对过滤 None，分母和 ≤0 返回 None）。"""
    pairs = [(a, b) for a, b in zip(num, den) if a is not None and b is not None]
    if not pairs:
        return None
    total_den = sum(b for _, b in pairs)
    if total_den <= 0:
        return None
    return sum(a for a, _ in pairs) / total_den


def avg_payout(divs: dict[int, float], eps_by_year: dict[int, float | None],
                this_year: int, n: int = 3) -> float | None:
    """近 n 个完整财年平均派息率（每股分红/每股收益）。"""
    ratios = []
    for year in range(this_year - 1, this_year - 1 - n, -1):
        ps, eps = divs.get(year), eps_by_year.get(year)
        if ps and eps and eps > 0:
            ratios.append(ps / eps)
    return sum(ratios) / len(ratios) if ratios else None


def turnover_days(turnover: float | None) -> float | None:
    """周转率(次) → 周转天数。"""
    if turnover is None or turnover <= 0:
        return None
    return 365.0 / turnover


def days_change_pct(turn_series: list[float | None], window: int) -> float | None:
    """周转天数变化率（%）：正数=恶化（变慢），负数=改善。

    例：存货周转 2.0→1.0 次，天数 182→365，变化 +100%（恶化一倍）。
    """
    turns = [t for t in turn_series if t is not None]
    if len(turns) < 2:
        return None
    w = min(window, len(turns))
    now, then = turnover_days(turns[-1]), turnover_days(turns[-w])
    if now is None or then is None:
        return None
    return (now / then - 1.0) * 100


def finite_or_none(v: float | None) -> float | None:
    if v is None or math.isnan(v) or math.isinf(v):
        return None
    return v
