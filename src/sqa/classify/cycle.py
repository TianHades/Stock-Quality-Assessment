"""周期性判定（01 号文档 §5.1）：R1 行业清单 + R2 量化特征。"""
from __future__ import annotations

from sqa.classify.models import RuleResult, Verdict
from sqa.config import Thresholds

# R1 周期行业关键词（对数据商行业字符串做子串匹配，精确度约申万二级/概念级）。
# 已知边界："铜"会命中"铜缆"类科技行业、"煤矿"等——命中后由 R2/人工复核兜底，
# 词表随金标准测试集校准迭代。
CYCLE_INDUSTRY_KEYWORDS: tuple[str, ...] = (
    # 黑色/能源
    "钢", "煤", "石油", "石化", "油气",
    # 化工
    "化工", "化学", "化纤", "钛白粉", "纯碱", "农药",
    # 有色/资源
    "有色", "采掘", "矿业", "工业金属", "能源金属", "小金属",
    "黄金", "白银", "稀土", "铜", "铝", "锌", "镍", "钴", "锂矿",
    # 航运港口/造船/水运
    "航运", "水运", "港口", "船舶",
    # 工程机械
    "工程机械",
    # 建材
    "水泥", "玻璃", "玻纤", "耐火材料",
    # 面板/存储（截胡科技）
    "面板", "显示器件", "存储",
    # 养殖（截胡消费）
    "养殖", "生猪", "肉鸡", "水产", "渔业",
    # 化工细分（tushare 行业串）
    "塑料", "橡胶", "染料", "焦炭",
    # 地产
    "房地产", "地产",
)


def is_cycle_industry(industry: str) -> bool:
    """R1：行业字符串命中周期清单。

    "日用化工"含"化工"但属化妆品消费（如上海家化），显式白名单排除；
    "元器件"不在此列（京东方等由 R2 量化特征兜底，避免误伤整个电子行业）。
    """
    ind = industry or ""
    if "日用化工" in ind:
        return False
    return any(kw in ind for kw in CYCLE_INDUSTRY_KEYWORDS)


def is_cycle_quantitative(
    margins: list[float | None],
    profits: list[float | None],
    cfg: Thresholds,
) -> RuleResult:
    """R2：毛利率极差 + 净利润最大回撤，两条 AND。

    margins: 年度毛利率序列（%，按年份升序，可含 None）
    profits: 年度归母净利润序列（同长度口径，可含 None）

    回撤口径是"先峰后谷"：区分"从高点跌落"（周期）与"从低基数长大"（成长）。
    金标准教训：宁德 10 年利润涨 25 倍 + 毛利率中枢下移，峰谷比口径会误判为周期；
    用最大回撤后单调成长公司回撤小，不再误触发。
    """
    pairs = [
        (m, p) for m, p in zip(margins, profits)
        if m is not None and p is not None
    ]
    if len(pairs) < cfg.min_years_for_quant:
        return RuleResult("R2_quant", Verdict.SKIP, f"有效年数 {len(pairs)} < {cfg.min_years_for_quant}")

    gm_values = [m for m, _ in pairs]
    gm_range = max(gm_values) - min(gm_values)

    profit_values = [p for _, p in pairs]
    max_dd = _max_drawdown(profit_values)

    gm_hit = gm_range > cfg.gm_range_pp
    profit_hit = max_dd > cfg.np_drawdown

    detail = (f"毛利率极差 {gm_range:.1f}pp（阈值 {cfg.gm_range_pp}），"
              f"净利润最大回撤 {max_dd:.0%}（阈值 {cfg.np_drawdown:.0%}）")
    if gm_hit and profit_hit:
        return RuleResult("R2_quant", Verdict.HIT, detail)
    return RuleResult("R2_quant", Verdict.MISS, detail)


def _max_drawdown(values: list[float]) -> float:
    """先峰后谷的最大跌幅；盈利转亏损视为 100%。峰值 ≤0 的阶段跳过。"""
    best_dd, peak = 0.0, float("-inf")
    for v in values:
        if v > peak:
            peak = v
            continue
        if peak > 0:
            dd = 1.0 if v <= 0 else 1.0 - v / peak
            best_dd = max(best_dd, dd)
    return best_dd
