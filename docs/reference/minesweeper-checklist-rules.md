<!--
来源：financial-report-minesweeper （https://github.com/terancejiang/financial-report-minesweeper）
版本锚点：main @ 483d3fc（2026-04-16）
内联日期：2026-10-18
说明：原项目已并入本工程并删除本地副本；本文件是规则事实源的本地快照，规则计数以本文件编号为准（32 条 = L0 2 + L1–L6 30）。
原始自述为“28 条”，系 v1.0 口径；v1.1 增加 5.8/5.9 后编号规则共 32 条，其中 L0 两道门槛通常不计入“30 条”口径。
-->

# 财报排雷检查规则 (Minesweeper Checklist Rules)

本文档定义 28 条排雷规则的详细阈值、数据字段映射和评估逻辑。
供 SKILL.md 在执行 Phase 3（规则检查）时参照。

---

## 判定标准

每条规则返回以下四种判定之一：

- **PASS**: 未发现异常
- **WARN**: 轻微异常或边界信号（计入 WARN 分数）
- **FAIL**: 显著异常（计入 FAIL 分数）
- **SKIP**: 数据不足，标注原因

---

## Layer 0: 门槛检查（一票否决）

### Rule 0.1 — 审计意见

- **数据源**: `data.audit[0].audit_result`
- **PASS**: `audit_result` 包含 "标准无保留" 或 "无保留意见"（且不含"保留"以外的修饰词）
- **FAIL**: 任何其他意见类型 → 风险等级直接定为"直接排除"
- **SKIP**: 审计数据为空

### Rule 0.2 — 按时披露

- **数据源**: `data.audit[0].ann_date` 和 `data.audit[0].end_date`
- **逻辑**: 年报的 `end_date` 为 YYYYMMDD 格式（如 20241231），`ann_date` 是公告日期
- **PASS**: `ann_date` 在次年 4 月 30 日之前（即 end_date 年份 +1 年的 0430）
- **FAIL**: `ann_date` 超过截止日
- **SKIP**: `ann_date` 缺失

---

## Layer 1: 利润表信号

### Rule 1.1 — 毛利率异常高 + 大幅波动

- **数据源**: `data.indicators[].grossprofit_margin` + `data.peers.peers[].grossprofit_margin`
- **计算**:
  - `gm_current` = 最新年度毛利率
  - `gm_prev` = 上一年度毛利率
  - `gm_yoy_change` = `gm_current - gm_prev`（单位: 百分点 pp）
  - `peer_median_gm` = 同行毛利率中位数
  - `gm_vs_peer` = `gm_current - peer_median_gm`
- **PASS**: `|gm_yoy_change| <= 5pp` 且 `gm_vs_peer <= 15pp`（或无同行数据）
- **WARN**: `|gm_yoy_change| > 5pp` 或 `gm_vs_peer > 15pp`
- **FAIL**: `|gm_yoy_change| > 10pp` 且 `gm_vs_peer > 15pp`
- **说明**: 如果无同行数据，仅用 YoY 波动判断

### Rule 1.2 — 毛利率↑ + 应收↑ + 应付↓

- **数据源**: `data.indicators` (grossprofit_margin) + `data.balance` (accounts_receiv, acct_payable) + `data.income` (revenue)
- **计算**:
  - 条件 A: 毛利率 YoY 上升
  - 条件 B: 应收账款增速 > 营收增速
  - 条件 C: 应付账款 YoY 减少
- **PASS**: 不满足任何条件，或仅满足 1 个条件
- **WARN**: 满足 2 个条件
- **FAIL**: 3 个条件同时满足

### Rule 1.3 — 运费增长 << 收入增长

- **数据源**: PDF 年报附注（Tushare 无此字段）
- **指引**: 在附注的"销售费用"或"营业成本"明细中查找"运输费"/"运费"/"装卸费"
- **PASS**: 运费增速 >= 收入增速的 50%
- **WARN**: 运费增速 < 收入增速的 50%
- **FAIL**: 运费增速 < 收入增速的 25%
- **SKIP**: 运费数据未在附注中单独披露

### Rule 1.4 — 其他业务收入占比突增

- **数据源**: `data.income[].oth_biz_income` 和 `data.income[].revenue`
- **计算**:
  - `ratio` = `oth_biz_income / revenue * 100`
  - `ratio_prev` = 上一年度同比
  - `ratio_change` = `ratio - ratio_prev`
- **PASS**: `ratio <= 5%` 或 `ratio_change <= 3pp`
- **WARN**: `ratio > 5%` 且 `ratio_change > 3pp`
- **FAIL**: `ratio > 15%` 或 `ratio_change > 10pp`

### Rule 1.5 — 费用率异常下降

- **数据源**: `data.income[]` (sell_exp, admin_exp, fin_exp, revenue) + `data.peers`
- **计算**:
  - `expense_ratio` = `(sell_exp + admin_exp + fin_exp) / revenue * 100`
  - `avg_3yr` = 近 3 年费用率均值
  - `drop` = `avg_3yr - expense_ratio_current`
- **PASS**: `drop <= 3pp`
- **WARN**: `drop > 3pp` 或 费用率显著低于同行中位数
- **FAIL**: `drop > 5pp`

### Rule 1.6 — 资产减值损失暴增

- **数据源**: `data.income[].assets_impair_loss` + `data.income[].credit_impa_loss` + `data.income[].n_income_attr_p`
- **计算**:
  - `total_impair` = `|assets_impair_loss| + |credit_impa_loss|`（注意减值可能为负数表示损失）
  - `impair_yoy` = YoY 变动百分比
  - `impair_to_profit` = `total_impair / |n_income_attr_p| * 100`
- **PASS**: `impair_yoy <= 50%` 且 `impair_to_profit <= 5%`
- **WARN**: `impair_yoy > 50%`
- **FAIL**: `impair_yoy > 100%` 或 `impair_to_profit > 5%`

---

## Layer 2: 现金流信号

### Rule 2.1 — 经营 CF 优异 + 投资 CF 持续大额为负

- **数据源**: `data.cashflow[]` (n_cashflow_act, n_cashflow_inv_act)，取 5-10 年数据
- **计算**:
  - 逐年检查: `n_cashflow_act > 0` 且 `|n_cashflow_inv_act| > n_cashflow_act * 0.8`
  - 统计满足条件的年数
- **PASS**: 满足条件的年数 <= 1
- **WARN**: 满足条件的年数 = 2-3
- **FAIL**: 满足条件的年数 >= 4，且投资 CF 绝对值持续大于经营 CF
- **说明**: 正常扩张企业投资 CF 为负是正常的，但如果持续大幅超过经营 CF 需要警惕

### Rule 2.2 — 经营 CF 持续为负

- **数据源**: `data.cashflow[].n_cashflow_act`，取 5-10 年
- **计算**: 统计 `n_cashflow_act < 0` 的年数和连续为负的最长年数
- **PASS**: 近 5 年中为负的年数 <= 1
- **WARN**: 近 5 年中 2 年为负
- **FAIL**: 连续 3+ 年为负

### Rule 2.3 — 高现金 + 高息借债

- **数据源**: `data.balance[]` (money_cap, st_borr, lt_borr, bond_payable) + `data.income[]` (fin_exp)
- **计算**:
  - `interest_bearing_debt` = `st_borr + lt_borr + bond_payable`（取最新期）
  - `cash` = `money_cap`
  - `implied_rate` = `|fin_exp| / interest_bearing_debt * 100`（粗估利率）
  - 基准利率参考: 约 3-4%（当前 LPR 水平）
- **PASS**: `cash < interest_bearing_debt * 0.5` 或 `implied_rate <= 基准 + 2pp`
- **WARN**: `cash > interest_bearing_debt * 0.5` 且 `implied_rate > 基准 + 2pp`
- **FAIL**: `cash > interest_bearing_debt` 且 `implied_rate > 基准 + 4pp`
- **说明**: 账上大量现金却高息借债 = 经典造假信号，现金可能是假的

---

## Layer 3: 资产负债表信号

### Rule 3.1 — 应收增速 > 收入增速

- **数据源**: `data.balance[]` (accounts_receiv) + `data.income[]` (revenue)
- **计算**:
  - `ar_growth` = 应收账款 YoY 增速
  - `rev_growth` = 营收 YoY 增速
  - `ratio` = `ar_growth / rev_growth`（当 rev_growth > 0 时）
- **PASS**: `ratio <= 1.5` 或应收账款金额很小（< 营收 5%）
- **WARN**: `ratio > 1.5`
- **FAIL**: `ratio > 2.0`
- **边界**: 如果营收下降但应收增长，直接 WARN

### Rule 3.2 — 存货周转率↓ + 毛利率↑（大概率造假组合信号）

- **数据源**: `data.indicators[]` (inv_turn, grossprofit_margin)
- **计算**:
  - `inv_turn_change` = 存货周转率 YoY 变化百分比
  - `gm_change` = 毛利率 YoY 变化（pp）
- **PASS**: 不满足组合条件
- **WARN**: `inv_turn_change < -10%` 且 `gm_change > 0`
- **FAIL**: `inv_turn_change < -20%` 且 `gm_change > 3pp`
- **特殊**: 此规则 FAIL 时触发组合加分 +10

### Rule 3.3 — 在建工程不转固

- **数据源**: `data.balance[]` (cip, fix_assets)，取 3-5 年
- **计算**:
  - 逐年检查: CIP 增长 >30% 但固定资产未相应增长
  - 统计持续增长年数
- **PASS**: CIP 不存在或未持续增长
- **WARN**: CIP 增长 >30% 且固定资产增长率 < CIP 增长率的 50%
- **FAIL**: 上述情况持续 3+ 年

### Rule 3.4 — 长期待摊费用大增

- **数据源**: `data.balance[].lt_amort_deferred_exp`
- **计算**: `yoy_change` = YoY 变动百分比
- **PASS**: `yoy_change <= 50%` 或金额很小（< 总资产 1%）
- **WARN**: `yoy_change > 50%`
- **FAIL**: `yoy_change > 100%`

### Rule 3.5 — 坏账计提比例低于同行

- **数据源**: `data.balance[]` (accounts_receiv) + `data.income[]` (credit_impa_loss) + `data.peers`
- **计算**:
  - `bad_debt_ratio` = `|credit_impa_loss| / accounts_receiv * 100`
  - 与同行对比（使用同行的 ar_turn 作为代理指标）
- **PASS**: 比例不显著低于同行
- **WARN**: 比例低于同行中位数
- **FAIL**: 比例低于同行中位数 50%
- **SKIP**: 如果应收账款极小或同行数据不足

---

## Layer 4: 交叉验证（权重最高）

### Rule 4.1 — 经营 CF / 净利润 < 1

- **数据源**: `data.cashflow[].n_cashflow_act` + `data.income[].n_income_attr_p`，取 5+ 年
- **计算**:
  - 逐年: `cf_to_profit` = `n_cashflow_act / n_income_attr_p`（当净利润 > 0）
  - 统计 `cf_to_profit < 1` 的年数
- **PASS**: 近 5 年中仅 0-1 年 < 1
- **WARN**: 近 5 年中 2 年 < 1
- **FAIL**: 连续 3+ 年 < 1
- **说明**: 这是最核心的验证。优秀企业此比值持续 > 1

### Rule 4.2 — 销售收现 / 营收 < 1

- **数据源**: `data.cashflow[].c_recp_prov_sg_act` + `data.income[].revenue`
- **计算**:
  - `cash_to_rev` = `c_recp_prov_sg_act / (revenue * 1.13)`
    （含增值税调整，A股一般税率 13%，实际应根据行业调整）
  - 简化版: `c_recp_prov_sg_act / revenue`（不含税调整，阈值相应调低）
- **PASS**: `cash_to_rev >= 0.9`（持续多年）
- **WARN**: `cash_to_rev < 0.9`
- **FAIL**: `cash_to_rev < 0.8` 持续 2+ 年

### Rule 4.3 — 利润膨胀 → 资产膨胀

- **数据源**: `data.income[]` (revenue, n_income_attr_p) + `data.balance[]` (total_assets)
- **计算**:
  - `asset_growth` = 总资产 YoY 增速
  - `rev_growth` = 营收 YoY 增速
  - `profit_growth` = 净利润 YoY 增速
  - 异常信号: 资产增速 >> 营收增速，且利润增长
- **PASS**: `asset_growth <= rev_growth * 1.5` 或利润未增长
- **WARN**: `asset_growth > rev_growth * 2` 且利润增长
- **FAIL**: `asset_growth > rev_growth * 3` 且利润增长，连续 2+ 年

### Rule 4.4 — 核心利润 vs 净利润背离

- **数据源**: `data.income[]` (revenue, oper_cost, sell_exp, admin_exp, fin_exp, n_income_attr_p)
- **计算**:
  - `core_profit` = `revenue - oper_cost - sell_exp - admin_exp - fin_exp`
  - `divergence` = `|core_profit - n_income_attr_p| / |n_income_attr_p| * 100`
- **PASS**: `divergence <= 20%`
- **WARN**: `divergence > 20%`
- **FAIL**: `divergence > 40%`
- **说明**: 大幅背离说明利润主要来自非经营性因素

### Rule 4.5 — 净利润增长 + FCF 持续为负

- **数据源**: `data.income[].n_income_attr_p` + `data.indicators[].fcff`（或自行计算 FCF = 经营 CF - Capex）
- **计算**:
  - 逐年检查: 净利润 > 0 且增长，但 FCF < 0
  - 统计满足条件的年数
- **PASS**: 满足条件 0-1 年
- **WARN**: 满足条件 2 年
- **FAIL**: 满足条件 3+ 年

---

## Layer 5: 非财务信号

### Rule 5.1 — 更换审计机构

- **数据源**: `data.audit[].audit_agency`，取多年数据
- **计算**: 比较各年度的事务所名称是否变化
- **PASS**: 连续多年使用同一家事务所
- **WARN**: 更换过 1 次
- **FAIL**: 更换过 2 次及以上

### Rule 5.2 — 大股东减持

- **数据源**: `data.holders[]`，取最近 2-3 期
- **计算**: 比较第一大股东（或控股股东）在不同报告期的 hold_ratio
- **PASS**: 持股比例稳定或增加
- **WARN**: 持股比例连续下降
- **FAIL**: 持股比例大幅下降（>5pp）
- **SKIP**: 股东数据不足

### Rule 5.3 — 财务总监频繁更换

- **数据源**: PDF 年报"董事、监事、高级管理人员"章节
- **指引**: 搜索"财务总监"/"首席财务官"/"CFO"，查看是否标注"本年离任"
- **PASS**: 无异常变动
- **WARN**: 本年有 CFO 变更
- **FAIL**: 近 2-3 年多次变更
- **SKIP**: 无法从当期年报判断

### Rule 5.4 — 独董集体辞职

- **数据源**: PDF 年报"董事、监事、高级管理人员"章节
- **指引**: 搜索"独立董事"，查看是否有多名标注"辞职"
- **PASS**: 无异常
- **WARN**: 1 名独董辞职
- **FAIL**: 2 名及以上独董同期辞职
- **SKIP**: 无法从当期年报判断

### Rule 5.5 — 可疑供应商/客户

- **数据源**: PDF 附注"前五名客户"/"前五名供应商"
- **指引**: 读附注中对应表格
- **PASS**: 前 5 大客户合计占比 < 30%，且客户名称正常
- **WARN**: 前 5 大客户合计占比 > 50%，或客户为不透明实体
- **FAIL**: 单一客户占比 > 50%

### Rule 5.6 — 跨行业频繁收购

- **数据源**: PDF 董事会报告"投资状况分析"
- **指引**: 读董事会报告中的投资/收购部分
- **PASS**: 无跨行业收购或仅有 1 次相关行业收购
- **WARN**: 有跨行业收购
- **FAIL**: 多次跨行业收购

### Rule 5.7 — 商誉过大

- **数据源**: `data.balance[].goodwill` + `data.balance[].total_hldr_eqy_exc_min_int`
- **计算**: `goodwill_ratio` = `goodwill / total_hldr_eqy_exc_min_int * 100`
- **PASS**: `goodwill_ratio <= 20%` 或 goodwill 为 0/null
- **WARN**: `goodwill_ratio > 20%`
- **FAIL**: `goodwill_ratio > 40%`

### Rule 5.8 — 其他应收款异常

- **数据源**: `data.balance[].oth_receiv` + `data.balance[].total_assets` + PDF 年报
- **计算**:
  - `ratio` = `oth_receiv / total_assets * 100`
  - `yoy_change` = 其他应收款 YoY 变动百分比
- **PASS**: `ratio <= 3%` 且 `yoy_change <= 30%`
- **WARN**: `ratio > 3%` 或 `yoy_change > 30%`
- **FAIL**: `ratio > 5%` 或 `yoy_change > 50%`
- **补充检查** (PDF): 在年报全文中搜索 "非经营性占用" — 如果该段落标注 "适用"，直接 FAIL
- **说明**: 其他应收款是关联方资金占用的常见藏身之处。大额其他应收款 + 关联方往来 = 经典资金占用信号

### Rule 5.9 — 监管处罚/立案调查

- **数据源**: PDF 年报全文
- **指引**: 在年报文本中搜索以下关键词: `立案`, `警示函`, `处罚决定`, `监管措施`, `行政处罚`, `纪律处分`, `通报批评`, `公开谴责`
- **判断**:
  - 匹配后读取上下文，区分以下情况:
    - A. 公司/控股股东/实控人被立案调查 → FAIL
    - B. 公司/董监高收到警示函或行政处罚 → WARN
    - C. 仅涉及一般性合规整改、环保罚款等 → PASS
    - D. 搜索无匹配 → PASS
- **说明**: 监管处罚和立案调查是重大风险前兆。喜临门案例: 2024年报已披露证监局警示函(2023)和上交所口头警示(2024)，但公司后续被发现存在多年资金占用黑幕

---

## Layer 6: 行业特有风险

### Rule 6.1 — 农林渔牧行业

- **数据源**: `data.stock_info.industry`
- **高风险行业关键词**: 农业, 林业, 渔业, 牧业, 畜牧, 养殖, 种植, 饲料, 水产
- **PASS**: 不属于高风险行业
- **WARN**: 行业包含以上关键词
- **说明**: 生物资产难以审计，是历史上造假高发区

### Rule 6.2 — 研发资本化比例

- **数据源**: PDF 附注"研发支出"或"开发支出"
- **指引**: 在附注中搜索"研发支出"/"开发支出"/"资本化"
- **计算**: `cap_ratio` = 资本化金额 / (资本化金额 + 费用化金额) * 100
- **PASS**: `cap_ratio <= 30%` 或全部费用化
- **WARN**: `cap_ratio > 30%`
- **FAIL**: `cap_ratio > 50%`
- **SKIP**: 附注中无明确的资本化/费用化拆分

---

## 评分权重表

| 层级 | 每条 WARN | 每条 FAIL |
|------|----------|----------|
| Layer 1 利润表 | 2 | 5 |
| Layer 2 现金流 | 3 | 6 |
| Layer 3 资产负债表 | 2 | 5 |
| Layer 4 交叉验证 | 3 | 7 |
| Layer 5 非财务 | 1 | 3 |
| Layer 6 行业 | 1 | 3 |

## 组合加分

| 条件 | 加分 |
|------|------|
| Rule 3.2 FAIL（存货周转↓ + 毛利率↑） | +10 |
| Rule 2.3 FAIL + Rule 4.1 FAIL（高现金高息借债 + CF/利润 < 1） | +8 |
| Rule 1.2 FAIL + Rule 3.1 FAIL（毛利率↑应收↑ + 应收增速超收入） | +6 |

## 风险等级映射

| 得分 | 等级 |
|------|------|
| 0-10 | 低风险 |
| 11-25 | 中风险 |
| 26-45 | 高风险 |
| 46+ | 极高风险 |
| Layer 0 任一 FAIL | 直接排除 |
