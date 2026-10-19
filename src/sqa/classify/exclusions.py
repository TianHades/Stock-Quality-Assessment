"""前置排除（01 号文档 §2）：ST / 医药 / 金融，命中即终止分析。"""
from __future__ import annotations

# 名称标记：ST/*ST/退市整理
_NAME_PATTERNS = ("ST", "退")

# 医药生物全产业链（申万一级：医药生物）
# 注意：不用裸"药"字——会误伤"农药"（基础化工，属周期清单）
_PHARMA_KEYWORDS = (
    "医药", "制药", "中药", "中成药", "成药", "原料药", "医疗", "生物",
    "器械", "疫苗", "体外诊断", "CXO",
)

# 银行 / 非银金融（申万一级）
_FINANCE_KEYWORDS = ("银行", "证券", "保险", "信托", "多元金融", "期货")


def exclusion_reason(name: str, industry: str) -> str | None:
    """命中排除返回原因文本，未命中返回 None。"""
    for pattern in _NAME_PATTERNS:
        if pattern in (name or ""):
            return f"名称含风险标记: {name}"

    ind = industry or ""
    for kw in _PHARMA_KEYWORDS:
        if kw in ind:
            return f"医药类行业: {ind}"
    for kw in _FINANCE_KEYWORDS:
        if kw in ind:
            return f"金融类行业: {ind}"
    return None
