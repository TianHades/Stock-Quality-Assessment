"""权重表与权重解析（03 号文档 §2-§3）：四主风格 + 红利叠加 + 缺失重分配。"""
from __future__ import annotations

DIMENSIONS: tuple[str, ...] = (
    "profitability", "growth", "cashflow", "health", "efficiency", "governance",
)

# 03 号文档 §2 主风格权重表（合计各为 100；均衡 = (成长+价值)/2 的落地值）
STYLE_WEIGHTS: dict[str, dict[str, float]] = {
    "growth":   {"profitability": 15.0, "growth": 35.0, "cashflow": 10.0,
                 "health": 10.0, "efficiency": 10.0, "governance": 20.0},
    "value":    {"profitability": 25.0, "growth": 10.0, "cashflow": 15.0,
                 "health": 20.0, "efficiency": 10.0, "governance": 20.0},
    "cyclical": {"profitability": 15.0, "growth": 5.0, "cashflow": 25.0,
                 "health": 30.0, "efficiency": 10.0, "governance": 15.0},
    "balanced": {"profitability": 20.0, "growth": 22.5, "cashflow": 12.5,
                 "health": 15.0, "efficiency": 10.0, "governance": 20.0},
}

# 红利叠加下限（03 号文档 §3）
_DIVIDEND_FLOOR = {"governance": 25.0, "cashflow": 20.0}


def base_weights(style: str) -> dict[str, float]:
    if style not in STYLE_WEIGHTS:
        raise ValueError(f"未知主风格: {style}")
    return dict(STYLE_WEIGHTS[style])


def apply_dividend_overlay(weights: dict[str, float]) -> dict[str, float]:
    """红利叠加：治理/现金流抬到下限，其余维度等比缩放使合计回到 100。

    注意：缩放对象是全部"未被抬升"的维度（含现金流本就达标的情形），
    否则合计会超过 100（03 号文档示例的算术勘误，见 §7 实现状态）。
    """
    lifted = {d: max(w, _DIVIDEND_FLOOR.get(d, 0.0)) for d, w in weights.items()}
    excess = sum(lifted.values()) - 100.0
    if excess <= 1e-9:
        return lifted

    lifted_dims = {d for d, floor in _DIVIDEND_FLOOR.items() if weights[d] < floor}
    others = [d for d in lifted if d not in lifted_dims]
    base_sum = sum(lifted[d] for d in others)
    if base_sum <= 0:
        return lifted
    factor = (base_sum - excess) / base_sum
    return {d: (lifted[d] * factor if d not in lifted_dims else lifted[d]) for d in lifted}


def redistribute(weights: dict[str, float], skip_dims: set[str]) -> dict[str, float]:
    """整维度 SKIP 时，权重按比例摊给其余维度。全部 SKIP 不该发生（上游拦截）。"""
    active = {d: w for d, w in weights.items() if d not in skip_dims}
    total = sum(active.values())
    if not active or total <= 0:
        return {d: 0.0 for d in weights}
    factor = 100.0 / total
    return {d: (w * factor if d not in skip_dims else 0.0) for d, w in weights.items()}


def resolve_weights(style: str, dividend_badge: bool,
                    skip_dims: set[str] | None = None) -> tuple[dict[str, float], bool]:
    """主入口：主风格 → 红利叠加 → 缺失重分配。返回 (最终权重, 是否叠加了红利)。"""
    weights = base_weights(style)
    overlay_applied = False
    if dividend_badge:
        overlay = apply_dividend_overlay(weights)
        overlay_applied = sum(overlay.values()) != sum(weights.values()) or overlay != weights
        weights = overlay
    if skip_dims:
        weights = redistribute(weights, skip_dims)
    return weights, overlay_applied
