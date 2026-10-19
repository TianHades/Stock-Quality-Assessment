"""模块公共辅助（纯函数）。"""
from __future__ import annotations

import math


def series(source: dict[int, dict], field: str, years: list[int]) -> list[float | None]:
    return [source.get(y, {}).get(field) for y in years]


def clean(values: list[float | None]) -> list[float]:
    return [v for v in values if v is not None and not math.isnan(v)]


def pctl_latest(values: list[float | None]) -> float | None:
    """最新值在序列中的分位（0~1）。序列不足 5 个点返回 None。"""
    vals = clean(values)
    if len(vals) < 5:
        return None
    latest = vals[-1]
    return sum(1 for v in vals if v <= latest) / len(vals)


def yoy_latest(values: list[float | None]) -> float | None:
    """最新同比。基期缺失/为零/为负返回 None。"""
    vals = values
    cur, prev = (vals[-1] if len(vals) >= 1 else None), (vals[-2] if len(vals) >= 2 else None)
    if cur is None or prev is None or prev <= 0 or cur is None:
        return None
    return cur / prev - 1.0


def std(values: list[float | None]) -> float | None:
    """样本标准差。不足 3 个点返回 None。"""
    vals = clean(values)
    if len(vals) < 3:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var)


def pct(v: float | None, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%" if v is not None else "NA"
