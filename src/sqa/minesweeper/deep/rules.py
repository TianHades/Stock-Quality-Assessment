"""深度模式规则判定：把 PDF 证据翻译成 PASS/WARN/FAIL/SKIP。

保守原则：证据不足 → SKIP（并在 detail 说明原因），不猜测；判断型规则只在
明确检出信号时给 WARN/FAIL，避免误伤（宁漏勿错）。
"""
from __future__ import annotations

from sqa.minesweeper.models import MsResult, MsVerdict

# 营收同比超过该阈值视为并表/重组阶跃（借壳/重大资产重组），运费对比不适用。
# 松发 603268 2025 营收 +7774%（恒力重工并表）属此类；正常高增（宁德 2022 +152%）
# 也不做运费对比（宁漏勿错）。
_MAX_REVENUE_GROWTH = 1.0


def rule_1_3(freight: dict, revenue_growth: float | None) -> MsResult:
    """运费增长 << 收入增长（WARN <50%，FAIL <25%）。"""
    if freight.get("status") != "ok":
        reason = freight.get("reason") or "PDF 未提供可用的运输费数据"
        return MsResult("1.3", "L1", MsVerdict.SKIP, f"深度模式：{reason}")
    if revenue_growth is None or revenue_growth <= 0:
        return MsResult("1.3", "L1", MsVerdict.SKIP,
                        "深度模式：营收增速缺失或≤0，运费增速对比不适用")
    if abs(revenue_growth) > _MAX_REVENUE_GROWTH:
        # 营收阶跃（借壳/并表/重组）下，运费与收入不可比（松发 603268 +7774%）
        return MsResult("1.3", "L1", MsVerdict.SKIP,
                        f"深度模式：营收同比 {revenue_growth:.0%}（并表/重组阶跃），"
                        "运费增速对比不适用")
    growth = freight["growth"]
    ratio = growth / revenue_growth
    detail = (f"运费增速 {growth:.1%} vs 营收增速 {revenue_growth:.1%}"
              f"（{ratio:.0%}，本期/上期 {freight['current']:,.0f}/{freight['previous']:,.0f}）")
    if growth < 0 and ratio < 0.25 - 1e-9:
        return MsResult("1.3", "L1", MsVerdict.FAIL, detail + "，运费增长远低于收入")
    if ratio < 0.25 - 1e-9:
        return MsResult("1.3", "L1", MsVerdict.FAIL, detail + "，运费增长远低于收入")
    if ratio < 0.5 - 1e-9:
        return MsResult("1.3", "L1", MsVerdict.WARN, detail + "，运费增长明显低于收入")
    return MsResult("1.3", "L1", MsVerdict.PASS, detail)


def rule_5_3(cfo: dict) -> MsResult:
    """财务负责人变动（检出聘任/离任/辞任等表述 → WARN，需复核时点）。"""
    if cfo.get("status") == "found":
        return MsResult("5.3", "L5", MsVerdict.WARN,
                        "深度模式：年报检出财务负责人变动（聘任/离任等）表述，需复核变动时点")
    if cfo.get("status") == "clear":
        return MsResult("5.3", "L5", MsVerdict.PASS,
                        "深度模式：年报未检出财务负责人离任表述")
    return MsResult("5.3", "L5", MsVerdict.SKIP,
                    "深度模式：年报未检索到财务负责人信息")


def rule_5_4(independent: dict) -> MsResult:
    """独立董事辞职：检出 1 处 → WARN；≥2 处 → FAIL（对齐上游"2 名以上独董辞职"）。"""
    if independent.get("status") == "found":
        count = independent.get("count", 1)
        if count >= 2:
            return MsResult("5.4", "L5", MsVerdict.FAIL,
                            f"深度模式：年报检出独立董事辞职表述 {count} 处（≥2 名，疑似集体辞职）")
        return MsResult("5.4", "L5", MsVerdict.WARN,
                        f"深度模式：年报检出独立董事辞职表述 {count} 处，需复核是否集体辞职")
    if independent.get("status") == "clear":
        return MsResult("5.4", "L5", MsVerdict.PASS,
                        "深度模式：年报未检出独立董事辞职表述")
    return MsResult("5.4", "L5", MsVerdict.SKIP,
                    "深度模式：年报未检索到独立董事信息")


def rule_5_5(customers: dict) -> MsResult:
    """客户集中度：单一客户 >50% FAIL；前五合计 >50% WARN；<30% PASS。"""
    if customers.get("status") != "ok":
        return MsResult("5.5", "L5", MsVerdict.SKIP,
                        "深度模式：年报未检索到可用的客户集中度数据")
    single = customers.get("single")
    total = customers.get("total")
    detail = "前五名客户合计占比 " + (f"{total:.1f}%" if total is not None else "未披露")
    if single is not None:
        detail += f"，单一客户 {single:.1f}%"
    if single is not None and single > 50:
        return MsResult("5.5", "L5", MsVerdict.FAIL, detail + "（单一客户>50%）")
    if total is not None and total > 50:
        return MsResult("5.5", "L5", MsVerdict.WARN, detail + "（前五合计>50%）")
    if total is not None and total < 30:
        return MsResult("5.5", "L5", MsVerdict.PASS, detail)
    return MsResult("5.5", "L5", MsVerdict.SKIP,
                    detail + "（30%–50% 区间，上游规则未定义判定）")


def rule_5_6(acquisitions: dict) -> MsResult:
    """跨行业收购：明确检出“跨行业/跨界” → WARN；其余待人工复核。"""
    if acquisitions.get("status") == "found":
        return MsResult("5.6", "L5", MsVerdict.WARN,
                        "深度模式：年报检出跨行业/跨界收购表述，需人工复核")
    if acquisitions.get("status") == "missing":
        return MsResult("5.6", "L5", MsVerdict.SKIP,
                        "深度模式：年报未检索到收购相关表述")
    return MsResult("5.6", "L5", MsVerdict.SKIP,
                    "深度模式：检出收购表述但未见“跨行业”，需人工复核是否跨行业")


def rule_5_9(regulatory: dict) -> MsResult:
    """监管处罚/立案调查：立案 → FAIL；警示函/处罚 → WARN；未检索到 → PASS。"""
    status = regulatory.get("status")
    if status == "fail":
        return MsResult("5.9", "L5", MsVerdict.FAIL,
                        "深度模式：年报检出公司/控股股东/董监高被立案调查表述")
    if status == "warn":
        return MsResult("5.9", "L5", MsVerdict.WARN,
                        "深度模式：年报检出警示函/处罚/监管措施等表述")
    if status == "clear":
        return MsResult("5.9", "L5", MsVerdict.PASS,
                        "深度模式：年报全文未检索到监管处罚/立案关键词")
    return MsResult("5.9", "L5", MsVerdict.SKIP,
                    "深度模式：检出监管关键词但语境不明，需人工复核")


def rule_6_2(rd: dict) -> MsResult:
    """研发资本化比例：>50% FAIL，>30% WARN，≤30% PASS。"""
    if rd.get("status") != "ok":
        reason = rd.get("reason") or "年报未披露研发资本化比例"
        return MsResult("6.2", "L6", MsVerdict.SKIP, f"深度模式：{reason}")
    ratio = rd["ratio"]
    detail = f"研发投入资本化比例 {ratio:.1%}"
    if ratio > 0.50:
        return MsResult("6.2", "L6", MsVerdict.FAIL, detail)
    if ratio > 0.30:
        return MsResult("6.2", "L6", MsVerdict.WARN, detail)
    return MsResult("6.2", "L6", MsVerdict.PASS, detail)


def supplement_5_8(occupation: dict) -> MsResult | None:
    """5.8 补充检查：年报“非经营性占用”段标注“适用” → 直接 FAIL；否则不覆盖快筛。"""
    if occupation.get("status") == "found":
        return MsResult("5.8", "L5", MsVerdict.FAIL,
                        "深度模式：年报“非经营性占用”段标注“适用”（关联方资金占用）")
    return None


def supplement_5_2(holder_events: dict) -> MsResult | None:
    """5.2 补充检查：年报明确披露大股东持股减少系无偿划转/可交换债换股 → PASS。

    仅当"划转/EB 披露"与"持股减少"同段出现时覆盖；未披露或语境不明不覆盖。
    """
    if holder_events.get("status") == "found":
        return MsResult("5.2", "L5", MsVerdict.PASS,
                        "深度模式：年报披露大股东持股减少系无偿划转/可交换债换股等"
                        "非二级市场减持事项（详见证据片段）")
    return None
