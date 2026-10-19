# 05 · 估值层（Valuation）

> StockQualityAnalysis 规则文档 · 第 5 篇（质地分析闭环的最后一块）
> 上游：01-分类（主风格+红利标签选方法）、04-周期位置（周期股双验证输入）
> 定位：质量层回答"是不是好公司"，本层回答"是不是好价格"

---

## 0. 方法选择矩阵（按主风格）

| 主风格 | 主方法 | 辅助 | 禁用/降级 |
|--------|--------|------|----------|
| value | **PB 自身 5 年分位** | PE 分位 | — |
| balanced | **PE 自身 5 年分位** | PB 分位 | — |
| growth | **PEG**（PE ÷ 扣非 3 年 CAGR×100） | PS 分位兜底 | 亏损/低增速时 PEG 降级 PS |
| cyclical | **PB 分位 + 周期位置双验证** | — | **禁用 PE**（顶部低 PE 是卖点非买点） |
| 红利标签（叠加） | **股息率利差**（股息率 − 10Y 国债，pp） | 股息率 5 年分位 | 可与任何主风格叠加 |

分位口径：**最新交易日**的值在自身近 5 年日频序列中的分位（与 01 号文档 V1 一致）。
最新交易日 PE 为 None（当期亏损）时按缺失处理，触发 PEG→PS / PE 分位→PB 降级，
不以数月前的旧 PE 冒充当前值（2026-10-19 校准）。

## 1. 各方法判定规则

### 1.1 value / balanced（分位法）

```
分位 < 30%           → undervalued
分位 30%-70%         → fair
分位 > 70%           → overvalued
```

附加信号：`深度价值` = PB < 1.0 且 PB 分位 < 20%。

### 1.2 growth（PEG）

```
扣非 CAGR ≤ 0 或 PE ≤ 0  → PEG 不适用，降级 PS 分位（同 1.1 分位带）
PEG < 1.0              → undervalued
PEG 1.0 - 2.0          → fair
PEG > 2.0              → overvalued
```

注意：PEG > 3 时附注"市场给足了成长定价，透支风险"。

### 1.3 cyclical（PB × 周期位置双验证）

以 PB 分位（<40% 低 / >60% 高）× 位置四态查表：

| 位置 \ PB | 低 PB | 高 PB |
|-----------|-------|-------|
| bottom 底部 | **undervalued**（双重确认低估区） | fair（市场已抢跑或资产质量疑虑） |
| recovery 复苏 | undervalued（左侧机会区） | fair |
| peak 高位 | fair（罕见组合：核查资产质量/盈利真实性） | **overvalued**（双重高估） |
| downturn 回落 | fair（左左侧，低 PB 可能越接越低） | **overvalued**（戴维斯双杀风险） |
| 位置缺失 | 仅 PB 单腿判断 + 附注 | 同左 |

**强制附注**："周期股禁用 PE——景气顶部低 PE 是周期见顶信号（盈利即将回落），
不是便宜"。

### 1.4 红利叠加（股息率利差）

```
利差 = 当前股息率(%) − 10Y 国债(%)
利差 ≥ 2pp 且股息率分位 ≥ 60%   → 收息吸引力高（类债逻辑成立）
利差 < 1pp                      → 类债逻辑弱化（机会成本太高）
其余                            → 中性
```

## 2. 输出格式

```json
{
  "module": "valuation", "style": "cyclical",
  "methods_applied": ["pb_percentile+cycle_double_check", "dividend_spread"],
  "metrics": {"pb_pctl": 0.22, "pe_pctl": null, "peg": null, "ps_pctl": null,
              "div_spread_pp": 3.52, "dv_pctl": 0.78},
  "verdict": "undervalued",
  "signals": ["深度价值"],
  "notes": ["周期股禁用 PE（顶部低 PE 是卖点非买点）"]
}
```

`verdict` 全集：undervalued / fair / overvalued / NA（数据不足）。
红利利差作为叠加信号写入 `signals`/`notes`，不覆盖主风格 verdict。

## 3. 实现状态（2026-10-19）

已代码化（`src/sqa/valuation/engine.py`，`sqa analyze` 末段输出）。
纯函数判定逻辑（各风格 verdict + 双验证矩阵 + PEG/利差）单独可测。

实跑校准记录：

- **risk_free_rate 的 NaN 泄出**：akshare 表格末行为空行，nan 直接传导到
  股息利差显示为 `nanpp`；且 `max(3.0, nan)` 在 Python 中偶然返回 3.0，
  此前 D1 阈值未暴露该污染。修复为倒序找有效行 + 值域校验（0-10%）；
- **神华（cyclical+红利）**：PB 分位 97% + 景气高位 → 双重高估；股息利差
  4.46pp 但股息率分位不足 60% → 中性（利差大是股价跌出来的，历史上曾更高）；
- **茅台（3 年窗口后 balanced）**：PE 分位 **19%** → 低估（balanced 主判 PE 分位）；
  若仍按 growth 口径，3 年扣非 CAGR 9.4% → **PEG ≈2.2 → 高估**（旧全窗口
  PEG 1.01 已不适用）；红利利差 **2.24pp** + 股息率分位 **92%** → `dividend_attractive`
  （不覆盖主 verdict）；
- **长电（3 年窗口后 growth）**：PEG 1.24 → 合理。

> 2026-10-19 起估值中的 CAGR 统一为近 3 年（4 个年度点），与 05 号文档
> §1.2 的"扣非 3 年 CAGR"一致。

## 4. 开放问题

1. 分位窗口 5 年是惯例起点，跨牛熊周期（7-10 年）窗口待回测校准；
2. 行业相对估值（当前行业 PE/PB 中位分位）需行业分组管道，与同行周转管道一并规划；
3. 红利利差的历史分位（当前利差 vs 自身利差历史）比单点利差更有判别力，待积累数据；
4. 股息率为 ttm 口径（daily_basic dv_ratio），与分红模块的财年口径并存，报告需注明。
