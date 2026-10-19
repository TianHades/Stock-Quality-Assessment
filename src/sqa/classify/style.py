"""成长/价值/均衡判定（01 号文档 §5.3）。纯函数，输入为已算好的度量。"""
from __future__ import annotations

from sqa.classify.models import RuleResult, Verdict
from sqa.config import Thresholds


def rule_g1(rev_cagr: float | None, np_dedt_cagr: float | None,
            cfg: Thresholds) -> RuleResult:
    """G1 成长：营收3年CAGR ≥ 15% 或 扣非净利3年CAGR ≥ 20%（扣非口径防一次性收益）。"""
    if rev_cagr is None and np_dedt_cagr is None:
        return RuleResult("G1", Verdict.SKIP, "增速数据不足（上市<3年或基期为负）")
    parts = []
    hit = False
    if rev_cagr is not None:
        parts.append(f"营收CAGR {rev_cagr:.1%}")
        hit = hit or rev_cagr >= cfg.growth_rev_cagr
    if np_dedt_cagr is not None:
        parts.append(f"扣非CAGR {np_dedt_cagr:.1%}")
        hit = hit or np_dedt_cagr >= cfg.growth_np_cagr
    return RuleResult("G1", Verdict.HIT if hit else Verdict.MISS,
                      f"{' / '.join(parts)}（门槛 {cfg.growth_rev_cagr:.0%} / {cfg.growth_np_cagr:.0%}）")


def rule_v1(rev_cagr: float | None, pe_pctl: float | None, pb_pctl: float | None,
            cfg: Thresholds) -> RuleResult:
    """V1 价值：(PE 或 PB 自身近5年分位 < 30%) 且 营收CAGR < 10%。"""
    if rev_cagr is None:
        return RuleResult("V1", Verdict.MISS, "增速数据不足，无法确认低增速")
    if pe_pctl is None and pb_pctl is None:
        return RuleResult("V1", Verdict.SKIP, "PE/PB 分位数据缺失（长期亏损等）")

    pctls = [p for p in (pe_pctl, pb_pctl) if p is not None]
    cheap = min(pctls) < cfg.value_pctl
    slow = rev_cagr < cfg.value_rev_cagr_max
    detail = f"PE分位 {_fmt(pe_pctl)} / PB分位 {_fmt(pb_pctl)}（门槛 {cfg.value_pctl:.0%}），营收CAGR {rev_cagr:.1%}（上限 {cfg.value_rev_cagr_max:.0%}）"
    verdict = Verdict.HIT if (cheap and slow) else Verdict.MISS
    return RuleResult("V1", verdict, detail)


def decide_style(rev_cagr: float | None, np_dedt_cagr: float | None,
                 pe_pctl: float | None, pb_pctl: float | None,
                 cfg: Thresholds) -> tuple[str, tuple[str, ...]]:
    """主风格裁决：成长优先于价值（高增速+低估值判成长并留信号），兜底均衡。

    返回 (style, signals)。signals 为报告引用的补充信号元组。
    """
    g1 = rule_g1(rev_cagr, np_dedt_cagr, cfg)
    v1 = rule_v1(rev_cagr, pe_pctl, pb_pctl, cfg)

    pctls = [p for p in (pe_pctl, pb_pctl) if p is not None]
    is_cheap = bool(pctls) and min(pctls) < cfg.value_pctl

    if g1.verdict is Verdict.HIT:
        signals = ("low_valuation_percentile",) if is_cheap else ()
        return "growth", signals
    if v1.verdict is Verdict.HIT:
        return "value", ()
    return "balanced", ()


def _fmt(p: float | None) -> str:
    return f"{p:.0%}" if p is not None else "NA"
