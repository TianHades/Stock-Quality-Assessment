"""金标准测试集（01 号文档 §8）：预期值可含多个可接受选项（None 表示不校验该字段）。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from sqa.classify.pipeline import classify_stock
from sqa.config import PROJECT_ROOT

# 金标准固定无风险利率快照：D1 阈值 = max(3.0%, rf+1.5pp) 会随 10Y 国债波动，
# 不固定时同一份数据在不同日期会得到不同结论（长电 3.01% 在 rf=1.50% 时 HIT、
# rf=1.65% 时 MISS）。金标准用于回归，必须可复现；分析主线仍用实时利率。
GOLDEN_RISK_FREE_RATE = "0.015"


@dataclass(frozen=True)
class GoldenCase:
    symbol: str
    name: str
    style: frozenset[str] | None      # 可接受的主风格集合；None=不校验
    badges: frozenset[str] | None     # 可接受的附加标签集合（精确匹配）；None=不校验
    tag: str | None                   # 行业标签；None=不校验


# 预期基于 2026-10-19 校准（3 年 CAGR 口径）：长江电力 3 年营收 CAGR 18.3% →
# growth；贵州茅台 3 年 10.8%/9.4% → balanced（badge 在固定 rf 下为 dividend）。
GOLDEN: tuple[GoldenCase, ...] = (
    GoldenCase("601088", "中国神华", frozenset({"cyclical"}), frozenset({"dividend"}), "other"),
    GoldenCase("600900", "长江电力", frozenset({"growth"}), frozenset({"dividend"}), "other"),
    GoldenCase("600519", "贵州茅台", frozenset({"growth", "balanced"}), frozenset({"dividend"}), "consumer"),
    GoldenCase("300750", "宁德时代", frozenset({"growth"}), frozenset(), "tech"),
    GoldenCase("600019", "宝钢股份", frozenset({"cyclical"}), None, "other"),
    GoldenCase("600309", "万华化学", frozenset({"cyclical"}), None, "other"),
    GoldenCase("002714", "牧原股份", frozenset({"cyclical"}), None, "consumer"),
    GoldenCase("000725", "京东方A", frozenset({"cyclical"}), None, "tech"),
    GoldenCase("000333", "美的集团", frozenset({"balanced", "value", "growth"}), None, "consumer"),
    # 双汇 2026-10 校准：D1/D2/D3 均过但派息率 99% 贴警戒线，D4 分红/FCF≈1.98
    # 被正确拦截（超能力分红靠存量现金维持）——红利预期撤回，规则不改
    GoldenCase("000895", "双汇发展", frozenset({"value", "balanced"}), frozenset(), "consumer"),
    GoldenCase("600941", "中国移动", frozenset({"value", "balanced"}), frozenset({"dividend"}), "tech"),
    GoldenCase("600276", "恒瑞医药", frozenset({"OUT_OF_SCOPE"}), None, None),
    GoldenCase("600036", "招商银行", frozenset({"OUT_OF_SCOPE"}), None, None),
)


def run_golden(refresh: bool = False) -> list[dict]:
    """逐只判定并与预期比对，返回结果列表（不抛异常，失败信息在条目内）。

    运行期间强制使用固定无风险利率快照（GOLDEN_RISK_FREE_RATE），保证同一份
    数据可复现；结束后恢复环境变量。
    """
    previous = os.environ.get("RISK_FREE_RATE")
    os.environ["RISK_FREE_RATE"] = GOLDEN_RISK_FREE_RATE
    results = []
    try:
        for case in GOLDEN:
            try:
                actual = classify_stock(case.symbol, refresh=refresh)
            except Exception as exc:  # noqa: BLE001 - 金标准运行需汇总所有失败而非中断
                actual = {"style": "ERROR", "name": case.name,
                          "error": f"{type(exc).__name__}: {exc}"}

            style_ok = case.style is None or actual.get("style") in case.style
            actual_badges = frozenset(actual.get("badges") or ())
            badges_ok = case.badges is None or actual_badges == case.badges
            tag_ok = case.tag is None or actual.get("industry_tag") == case.tag

            results.append({
                "symbol": case.symbol, "name": case.name,
                "expected_style": sorted(case.style) if case.style else None,
                "actual_style": actual.get("style"),
                "style_ok": style_ok,
                "expected_badges": sorted(case.badges) if case.badges is not None else None,
                "actual_badges": sorted(actual_badges),
                "badges_ok": badges_ok,
                "expected_tag": case.tag, "actual_tag": actual.get("industry_tag"),
                "tag_ok": tag_ok,
                "pass": style_ok and badges_ok and tag_ok,
                "detail": actual,
            })
    finally:
        if previous is None:
            os.environ.pop("RISK_FREE_RATE", None)
        else:
            os.environ["RISK_FREE_RATE"] = previous
    _save(results)
    return results


def _save(results: list[dict]) -> None:
    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"golden_{time.strftime('%Y%m%d_%H%M')}.json"
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
