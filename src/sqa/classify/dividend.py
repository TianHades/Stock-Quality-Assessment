"""红利属性判定（01 号文档 §5.2）：D1-D4 四条 AND。纯函数，输入为已算好的度量。"""
from __future__ import annotations

from sqa.classify.models import RuleResult, Verdict
from sqa.config import Thresholds


def rule_d1(dv_ratios: list[float], risk_free: float, cfg: Thresholds) -> RuleResult:
    """D1 股息率：近3年均值 ≥ max(静态下限, 10Y国债+溢价)。单位：%。"""
    vals = [v for v in dv_ratios if v is not None]
    if not vals:
        return RuleResult("D1", Verdict.SKIP, "无股息率数据")
    avg = sum(vals) / len(vals)
    floor = max(cfg.yield_floor, risk_free * 100 + cfg.yield_rf_spread_pp)
    verdict = Verdict.HIT if avg >= floor else Verdict.MISS
    return RuleResult("D1", verdict, f"近3年平均股息率 {avg:.2f}%（阈值 {floor:.2f}%）")


def rule_d2(consecutive_years: int | None, cfg: Thresholds) -> RuleResult:
    """D2 连续性：连续分红年数 ≥ 阈值。"""
    if consecutive_years is None:
        return RuleResult("D2", Verdict.SKIP, "无分红记录")
    verdict = Verdict.HIT if consecutive_years >= cfg.consecutive_years else Verdict.MISS
    return RuleResult("D2", verdict, f"连续分红 {consecutive_years} 年（阈值 {cfg.consecutive_years}）")


def rule_d3(payout_avg: float | None, cfg: Thresholds) -> RuleResult:
    """D3 意愿与可持续：近3年平均派息率 ∈ [25%, 90%]；(90%,100%] 命中但附警示。"""
    if payout_avg is None:
        return RuleResult("D3", Verdict.SKIP, "无派息率数据")
    if cfg.payout_min <= payout_avg <= cfg.payout_warn:
        return RuleResult("D3", Verdict.HIT, f"近3年平均派息率 {payout_avg:.0%}")
    if cfg.payout_warn < payout_avg <= 1.0:
        return RuleResult(
            "D3", Verdict.HIT,
            f"近3年平均派息率 {payout_avg:.0%}，超过可持续性警戒线 {cfg.payout_warn:.0%}，警惕特别分红/借钱分红",
        )
    return RuleResult("D3", Verdict.MISS, f"近3年平均派息率 {payout_avg:.0%}，不在 [{cfg.payout_min:.0%}, 100%] 区间")


def rule_d4(cum_div: float | None, cum_fcf: float | None, cfg: Thresholds) -> RuleResult:
    """D4 分红能力：近3年累计分红 / 累计 FCF ≤ 上限。"""
    if cum_div is None or cum_fcf is None:
        return RuleResult("D4", Verdict.SKIP, "分红金额或 FCF 数据缺失")
    if cum_fcf <= 0:
        return RuleResult("D4", Verdict.MISS, f"累计 FCF 为 {cum_fcf:.0f}，无法覆盖分红")
    ratio = cum_div / cum_fcf
    verdict = Verdict.HIT if ratio <= cfg.fcf_cover_max else Verdict.MISS
    return RuleResult("D4", verdict, f"累计分红/累计FCF = {ratio:.2f}（上限 {cfg.fcf_cover_max}）")


def evaluate_dividend(results: list[RuleResult]) -> bool:
    """红利标签命中条件：D1-D4 全部 HIT（D3 的警示型 HIT 也算命中）。"""
    hit = [r for r in results if r.verdict is Verdict.HIT]
    return len(hit) == len(results) and len(results) == 4
