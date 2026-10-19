"""分类判定的输出模型（不可变）。判定词汇沿用 01 号文档：HIT / MISS / SKIP。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    SKIP = "SKIP"


@dataclass(frozen=True)
class RuleResult:
    """单条规则的判定结果。detail 必须人类可读，供证据链展示。"""

    rule: str
    verdict: Verdict
    detail: str = ""


@dataclass(frozen=True)
class StockBasic:
    symbol: str        # 6位代码，如 600519
    ts_code: str       # 带后缀，如 600519.SH
    name: str
    industry: str      # 数据商行业字符串（tushare industry）
    list_date: str     # YYYYMMDD


@dataclass(frozen=True)
class ClassificationOutput:
    """01 号文档 §7 的 JSON 输出结构。"""

    symbol: str
    name: str
    style: str                       # growth | value | cyclical | balanced | OUT_OF_SCOPE
    badges: tuple[str, ...] = ()     # dividend
    industry_tag: str = "other"      # consumer | tech | other
    confidence: str = "high"         # high | medium | low
    signals: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: dict = field(default_factory=dict)
    weight_table: str = ""           # 主风格同名，OUT_OF_SCOPE 为空
    modules: tuple[str, ...] = ()    # 专属分析模块清单（04 号文档对接点）
