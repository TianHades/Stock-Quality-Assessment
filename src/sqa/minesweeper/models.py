"""排雷判定的输出模型与计分常量（上游 checklist-rules.md 的计分权重表）。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MsVerdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True)
class MsResult:
    rule: str          # 如 "1.1" / "S1"
    layer: str         # "L0"~"L6" / "S"
    verdict: MsVerdict
    detail: str


# 每条 WARN / FAIL 的层级别计分（上游权重表，02 号文档 §2）
LAYER_POINTS: dict[str, dict[str, int]] = {
    "L0": {"WARN": 0, "FAIL": 0},   # L0 直接排除，不计分
    "L1": {"WARN": 2, "FAIL": 5},
    "L2": {"WARN": 3, "FAIL": 6},
    "L3": {"WARN": 2, "FAIL": 5},
    "L4": {"WARN": 3, "FAIL": 7},
    "L5": {"WARN": 1, "FAIL": 3},
    "L6": {"WARN": 1, "FAIL": 3},
    "S": {"WARN": 1, "FAIL": 3},
}


def score_results(results: list[MsResult]) -> int:
    total = 0
    for r in results:
        if r.rule == "S4":
            continue  # S4 只作为组合加分（+5），不再计层分（doc 02 §3）
        pts = LAYER_POINTS.get(r.layer, {}).get(r.verdict.value)
        total += pts or 0
    return total


def combo_bonus(results: list[MsResult]) -> tuple[int, list[str]]:
    """组合造假信号加分（上游 3 组 + 本系统 S4），返回 (加分, 命中说明)。"""
    failed = {r.rule for r in results if r.verdict is MsVerdict.FAIL}

    bonus, hits = 0, []
    if "3.2" in failed:
        bonus += 10
        hits.append("存货周转↓+毛利率↑ +10")
    if "2.3" in failed and "4.1" in failed:
        bonus += 8
        hits.append("高现金高息借债+CF/净利<1 +8")
    if "1.2" in failed and "3.1" in failed:
        bonus += 6
        hits.append("毛利↑应收↑应付↓+应收增速超收入 +6")

    s4 = next((r for r in results if r.rule == "S4"), None)
    if s4 is not None and s4.verdict is MsVerdict.FAIL:
        bonus += 5
        hits.append("高质押+减持组合 +5")
    return bonus, hits


def layer_score(results: list[MsResult]) -> dict[str, int]:
    """按层汇总得分（报告用；S4 仅组合加分，不计层分）。"""
    out: dict[str, int] = {}
    for r in results:
        if r.rule == "S4":
            continue
        pts = LAYER_POINTS.get(r.layer, {}).get(r.verdict.value) or 0
        out[r.layer] = out.get(r.layer, 0) + pts
    return out


def assess(results: list[MsResult],
           gate_extreme: int = 46, gate_high: int = 26, gate_mid: int = 11,
           ) -> dict:
    """风险闸门（02 号文档 §4）：
    L0 任一 FAIL → excluded；其余按 总分(层分+组合加分) 映射风险等级与 verdict。

    ``forceable``：仅"高风险(26-45)"允许 `--force` 继续；L0 排除与极高风险(46+)
    不可强制（避免 --force 绕过一票否决）。
    """
    base = score_results(results)
    bonus, combos = combo_bonus(results)
    total = base + bonus

    l0_fail = any(r.layer == "L0" and r.verdict is MsVerdict.FAIL for r in results)
    if l0_fail:
        risk_level, verdict, forceable = "excluded", "excluded", False
    elif total >= gate_extreme:
        risk_level, verdict, forceable = "extreme", "blocked", False
    elif total >= gate_high:
        risk_level, verdict, forceable = "high", "blocked", True
    elif total >= gate_mid:
        risk_level, verdict, forceable = "mid", "degraded", False
    else:
        risk_level, verdict, forceable = "low", "pass", False

    return {
        "base_score": base,
        "combo_bonus": bonus,
        "combos": combos,
        "total_score": total,
        "risk_level": risk_level,   # low | mid | high | extreme | excluded
        "verdict": verdict,         # pass | degraded | blocked | excluded
        "forceable": forceable,     # 仅 high 风险允许 --force
        "layer_scores": layer_score(results),
    }
