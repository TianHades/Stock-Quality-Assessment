"""锚点插值（03 号文档 §4.1）：三分段线性（0/50/100），支持反向锚点。

反向锚点（值越小分越高，如质押率）天然由递减锚点支持：
score_anchor(25, zero=50, fifty=20, hundred=5) 同样走线性插值。
"""
from __future__ import annotations


def score_anchor(value: float, zero: float, fifty: float, hundred: float) -> float:
    """分段线性插值到 [0,100]。断点：zero→0 分，fifty→50 分，hundred→100 分。"""
    if fifty == zero or hundred == fifty:
        raise ValueError(f"锚点重合: {zero}/{fifty}/{hundred}")

    def interp(v0: float, s0: float, v1: float, s1: float, x: float) -> float:
        return s0 + (s1 - s0) * (x - v0) / (v1 - v0)

    score = (
        interp(zero, 0.0, fifty, 50.0, value) if _between(value, zero, fifty)
        else interp(fifty, 50.0, hundred, 100.0, value)
    )
    return max(0.0, min(100.0, score))


def _between(x: float, a: float, b: float) -> bool:
    return (a <= x <= b) if a <= b else (b <= x <= a)
