# 02 · 排雷层集成方案（Minesweeper Integration）

> StockQualityAnalysis 规则文档 · 第 2 篇
> 上游：指标计算层（数据管道）
> 下游：01-类别判定（排雷通过后进入）、03-质量权重表（风险等级联动评级）

---

## 0. 定位

排雷层是 pipeline 的**一票否决闸门**：不判断股票好坏，只回答"这只股票的财报可不可信、
有没有爆雷前兆"。规则定义源自兄弟项目 `financial-report-minesweeper`（唐朝
《手把手教你读财报》方法论的程序化实现），**已于 2026-10-18 整体内联并删除上游本地副本**
（详见 [reference/minesweeper-provenance.md](reference/minesweeper-provenance.md)）；
本仓库补充 4 条上游缺失的 A 股高频雷规则，并以确定性 Python 引擎实现全部规则。

## 1. 复用决策

| 事项 | 决策 | 理由 |
|------|------|------|
| 规则定义（阈值/判定逻辑） | **内联为本地事实源** | 上游项目已归档删除，规则全文落在本仓库，版本锚点随代码走 |
| 规则引擎（代码实现） | 本仓库自建 | 上游以 Claude Code Skill 形态运行（AI 读清单逐条执行），本系统是 Python pipeline，需程序化实现 |
| 数据管道 | 借鉴其 tushare 取数设计（10 年三表+审计+股东+同行） | 同源数据，自建缓存与限速层 |
| PDF 类规则 | **深度模式已落地**（按需触发） | 见 §5：下载/解析年报 PDF，补齐 PDF 依赖规则 |

**规则事实源（本地快照）**：
- 规则全文：[`docs/reference/minesweeper-checklist-rules.md`](reference/minesweeper-checklist-rules.md)
  （原上游 `skill/references/checklist-rules.md`，`main @ 483d3fc`）；
- 原书依据：[`docs/reference/minesweeper-rule-rationale.md`](reference/minesweeper-rule-rationale.md)；
- 深度模式操作指引：[`docs/reference/pdf-deep-mode-guide.md`](reference/pdf-deep-mode-guide.md)；
- 版本锚点写入 `sqa/minesweeper/engine.py` 的 `SOURCE_VERSION`（commit hash，非日期）。

## 2. 规则总览（7 层 32 条：L0 2 + L1–L6 30，判定四级：PASS/WARN/FAIL/SKIP）

> 计数说明：上游文档曾出现“28 条 / 30 条”口径（后者不含 L0 两道门槛）。本工程统一按
> 规则文件编号计为 32 条；另加 Layer S 补充 4 条，规则槽位共 36 条。详见
> [reference/minesweeper-provenance.md](reference/minesweeper-provenance.md)。

| 层 | 主题 | 条数 | 代表规则 |
|----|------|------|---------|
| L0 | 门槛（一票否决） | 2 | 审计意见非标；年报逾期披露 |
| L1 | 利润表信号 | 6 | 毛利率异常高且大幅波动；毛利↑+应收↑+应付↓；运费增速<<收入增速；其他业务收入突增；费用率异常下降；减值暴增 |
| L2 | 现金流信号 | 3 | 投资 CF 持续吞掉经营 CF；经营 CF 连续 3 年为负；高现金+高息借债（存贷双高） |
| L3 | 资产负债表信号 | 5 | 应收增速>收入增速×2；存货周转↓+毛利率↑（造假组合）；在建工程不转固；长期待摊大增；坏账计提低于同行 |
| L4 | 交叉验证（权重最高） | 5 | 经营CF/净利连续<1；销售收现/营收<0.8；利润膨胀→资产膨胀；核心利润与净利背离>40%；净利增长+FCF持续为负 |
| L5 | 非财务信号 | 9 | 频繁换审计所；大股东减持；CFO 频繁更换；独董集体辞职；单一客户>50%；跨行业收购；商誉>40%净资产；其他应收款异常；立案调查 |
| L6 | 行业特有 | 2 | 农林牧渔（生物资产难审计）；研发资本化>50% |

计分：每条 WARN/FAIL 按层级加权（L4 最重：FAIL 7 分/条，L1/L3 次之 5 分），另有 3 组
组合造假信号加分（最高 +10）。风险等级映射：

| 总分 | 风险等级 |
|------|---------|
| 0–10 | 低风险 |
| 11–25 | 中风险 |
| 26–45 | 高风险 |
| 46+ | 极高风险 |
| L0 任一 FAIL | 直接排除 |

## 3. 本系统补充规则（Layer S，上游缺口）

对照 01 号文档架构里列的排雷维度，上游缺 4 项 A 股高频雷。计分权重对齐 L5（WARN 1 / FAIL 3）：

### S1 · 大股东股权质押率

- 数据源：tushare `pledge_stat`（或 akshare 股权质押接口）
- PASS：质押比例 ≤ 30%
- WARN：30%–50%
- FAIL：> 50%
- 说明：质押爆仓是 A 股大股东风险传导的第一通道，上游全未覆盖

### S2 · 短期偿债缺口

- 数据源：`balance`（money_cap, st_borr, non_cur_liab_due_1y）
- 计算：`coverage = money_cap / (st_borr + non_cur_liab_due_1y 中有息部分)`
- PASS：≥ 1.5；短期有息负债为 0 或极小（< 总资产 3%）视为无实质缺口 → PASS
  （2026-10 校准：原文档写 SKIP，实测零短债公司应视为最佳覆盖）
- WARN：1.0–1.5
- FAIL：< 1.0

### S3 · 利息保障倍数

- 数据源：`income`（利润总额 + 财务费用估算 EBIT / 利息支出）
- PASS：EBIT / 利息 ≥ 6
- WARN：3–6
- FAIL：< 3
- SKIP：利息支出极小（财务费用 < 营收 0.5%）

### S4 · 组合信号：高质押 + 减持

- 计算：质押率 > 30% 且（结合上游 Rule 5.2）大股东持股比例近 2 年下降 > 2pp
- 命中 → 计分 +5（对齐上游"组合加分"设计，不单列 FAIL）

## 4. 风险闸门（排雷结果如何驱动 pipeline）

| 排雷结果 | pipeline 行为 | 对 03 号质量评级的影响 |
|---------|--------------|----------------------|
| L0 FAIL | **出局**，分析终止；`--force` 不可绕过 | — |
| 46+ 极高风险 | **出局**，分析终止；`--force` 不可绕过 | — |
| 26–45 高风险 | 默认**出局**；`--force` 可继续（`forceable=True`） | 若强制继续：评级双降 + 强制风险横幅 |
| 11–25 中风险 | 通过，进入分类判定 | 评级降一档 + 报告显著警示 |
| 0–10 低风险 | 通过 | 无影响 |

配置化：闸门阈值为可调参数（YAML `minesweeper.base_rate/gate_*` → `ms_*` 字段映射）；`assess` 输出 `forceable` 字段，仅高风险可强制。

## 5. 两档执行模式

约 1/3 规则依赖年报 PDF 解析（运费、CFO/独董变动、客户集中度、跨行业收购、研发
资本化、监管处罚关键词扫描），单次分析需下载并解析数百页 PDF，代价高。设计两档：

| 模式 | 覆盖 | 数据源 | 用途 |
|------|------|--------|------|
| **快筛（默认）** | L0–L4 + L5 结构化部分（5.1/5.2/5.7/5.8）+ L6.1 + Layer S | 纯 tushare | pipeline 主线，全量股票可承受 |
| **深度（按需）** | 全部规则含 PDF 类（1.3、5.3–5.6、5.9、6.2）+ 5.8 补充检查 | tushare + 年报 PDF（本地 / URL / 巨潮自动下载） | 用户对个别标的深入分析时触发 |

**实现（2026-10-19 加固）**：

```bash
sqa minesweeper 600519 --mode deep                      # 巨潮自动检索最近年报
sqa minesweeper 600519 --mode deep --pdf "年报.pdf"      # 离线指定本地 PDF
sqa analyze 600519 --deep [--pdf ... | --pdf-url ...]    # 排雷环节使用深度模式
```

- 链路：`resolve_pdf`（本地 → URL → 缓存 → 巨潮搜索）→ `extract_pdf`（pdftotext/pypdf
  双后端，短文本自动换后端；文本按 sha1 缓存，保留物理页号）→ 证据抽取 → 7 条 PDF
  规则判定 + 5.8 补充检查；
- 判定策略：**数值型规则**（1.3/5.5/6.2/5.8 补充）证据可靠时自动判定；**判断型规则**
  （5.3/5.4/5.6/5.9）按句/表格行检出，原文证据供人工复核。5.9 解析"√是/√否"勾选项
  与按句否定语境（避免"是否被立案调查：□是 √否"误报 FAIL）；5.8 只认"√适用/√不适用"
  勾选项，未勾选项里的"不适用"不再提前清空；5.4 检出 ≥2 处独董辞职 → FAIL；
- 失败降级：PDF 获取失败（`skipped`）或路径/解析异常（`error`）时保留快筛结果，
  `deep` 字段记录原因，报告中不静默；文本量过少或非空页占比 <10% 也判为抽取失败，
  避免扫描版 PDF 产生假 PASS；`--pdf/--pdf-url` 未配深度模式会直接报参数错误，
  不再静默忽略；
- 证据优先级：`fails → warns → hits`，避免 JSON 里展示被否定的片段；
- 依赖：`pip install -e ".[pdf]"`（pypdf）；若系统有 poppler 的 `pdftotext` 则优先使用。
- 操作细节与关键词映射见 [reference/pdf-deep-mode-guide.md](reference/pdf-deep-mode-guide.md)。

## 6. 输出格式

```json
{
  "symbol": "600519",
  "mode": "deep",                 // quick | deep
  "verdict": "pass",              // pass | degraded(中风险通过) | blocked | excluded(L0)
  "total_score": 8,               // base_score + combo_bonus
  "forceable": false,             // 仅高风险(26-45)为 true，可 --force
  "risk_level": "low",            // low | mid | high | extreme
  "layer_scores": {"L1": 0, "L4": 0, "S": 0},
  "rules": [
    {"id": "0.1", "layer": "L0", "verdict": "PASS", "detail": "标准无保留意见"},
    {"id": "1.3", "layer": "L1", "verdict": "PASS", "detail": "运费增速 12.3% vs 营收增速 15.1%"},
    {"id": "5.5", "layer": "L5", "verdict": "WARN", "detail": "前五名客户合计占比 ..."}
  ],
  "deep": {
    "status": "ok",               // ok | skipped(无 PDF) | error(解析失败)
    "message": "年报 PDF 356 页（pypdf）",
    "pdf": {"path": "...", "source": "cninfo", "title": "2025年年度报告",
            "url": "...", "pages": 356, "backend": "pypdf"},
    "verdicts": {"1.3": "PASS", "5.3": "PASS", "5.4": "PASS", "5.5": "WARN",
                 "5.6": "SKIP", "5.9": "PASS", "6.2": "SKIP"},
    "evidence": {"5.5": {"status": "ok", "reason": "",
                          "hits": [{"page": 42, "keyword": "前五名客户", "snippet": "..."}]}}
  },
  "source_version": "checklist-rules.md@483d3fc（原 financial-report-minesweeper，2026-04-16 归档）"
}
```

> 快筛模式下 `mode="quick"`、`deep=null`，被跳过的规则在 `rules` 中保留
> `SKIP(深度模式可查)` 占位，不静默。

## 7. 实现状态（2026-10-19）

快筛模式已代码化（`src/sqa/minesweeper/`，CLI：`sqa minesweeper <code>`）：

- **已实现**：L0（2 条）、L1–L4（19 条，3.5 已接入同行坏账管道）、
  L5 结构化部分（5.1/5.2/5.7/5.8）、L6.1、Layer S 全部（S1–S4）；
- **深度模式（2026-10-19 加固）**：`--mode deep`（`sqa analyze --deep`）下载/解析年报
  PDF，补齐 1.3/5.3/5.4/5.5/5.6/5.9/6.2 七条 PDF 规则，并用 5.8 的“非经营性占用”段
  补充检查覆盖快筛结果。链路：本地 PDF / URL / 缓存 / 巨潮搜索 → pdftotext/pypdf
  双后端 → 按句/表格行证据抽取（勾选项解析、跨页客户明细、金额绑定）；
  PDF 获取/解析异常统一降级为 `deep.status=error`，不中断流水线；
- **3.5 同行坏账（2026-10-19 B 步）**：同行业年报取 `credit_impa_loss`/
  `accounts_receiv`，按市值前 8 家算坏账计提比例中位数；公司比例 <同行中位数
  →WARN，<50%→FAIL；应收账款<总资产1% 或同行不足3家→SKIP。同行 `income`/
  `balancesheet` 不支持多代码批量，按单票查询并缓存，冷启动约 5–6 秒；
- **数据**：显式 fields 年报三表（字段清单集中在 `src/sqa/data/fields.py`；
  2026-10-18 修正 `biz_tax_surchg` / `lt_amor_exp` / `c_fr_sale_sg` /
  `c_pay_acq_const_fiolta` / `non_cur_liab_due_1y` / `depr_fa_coga_dpba`，
  并对 `inv_turn`、`credit_impa_loss` 等可选列显式请求；此前不传 fields 时
  tushare 只返回默认列，导致 1.4/1.6/3.2/3.4/4.2/4.4/S2 等多条规则静默 SKIP），
  fina_audit / pledge_stat / top10_holders 独立取数，复用统一缓存层；
- **规则来源**：原 `financial-report-minesweeper` 已内联并删除本地副本
  （`main @ 483d3fc`），规则全文与依据见 `docs/reference/`。

实跑校准（茅台/神华/京东方）对上游规则的两处修正：

1. **4.4 核心利润公式补"税金及附加" + 改总净利口径**：上游公式漏该项，白酒等
   消费税重仓行业整体性误报背离；字段真实名为 `biz_tax_surchg`。同时核心利润
   是少数股东损益前口径，必须与总净利 `n_income` 比较（2026-10-18 校准）：
   神华 core/归母 40.3% vs core/总净利 18.1%、京东方 252.7% vs 310.9%，
   若继续对归母比较会把少数股东占比高的公司系统性误判；
2. **5.1 换所检测限近 10 年 + 名称归一化**：全历史窗口叠加事务所行业并购
   改名潮（天一→中和正信→天健正信→立信…），几乎所有老公司都会触发 FAIL，
   规则退化为噪声；归一化处理"立信"6 种写法变体。

实跑画像（2026-10-18 字段契约修复后，快筛）：

- 茅台 **9 分**（4.4 WARN 31%；修复前 6 分，多条规则因字段缺失被跳过）；
- 神华 **15 分 degraded**（3.5 FAIL：坏账计提 0.03% vs 同行中位数 0.99%；
  1.2/3.2/3.3/4.2/5.1 五条 WARN；B 步前为 10 分）；
- 京东方 **34 分 blocked**（3.5 FAIL 0.20% vs 0.64%、4.4 FAIL 311%、
  1.6 FAIL 减值占利润 69.5%、2.1 FAIL；B 步前为 29 分；风险被系统性低估）。

> 旧 `data_cache/` 是默认字段口径，字段名变更后缓存键已变化；升级后建议
> `--refresh` 或删缓存重取，并用新结果重跑金标准。

深度模式实跑（2026-10-19 加固后）：

- 600519：5.3 WARN（财务总监聘任变动）、5.9 WARN（年报披露"高级管理人员被实施留置"
  及"受到处罚及整改"）、5.5 PASS（前五客户 10.1%）、6.2 PASS（研发资本化 16.0%），
  总分 11 → `degraded`（中风险）；5.4/5.6 无明确信号；
- 000333：5.5 PASS（合计 13.2%、单一客户 8.4%）、5.6 SKIP（"跨行业项目"非收购）、
  5.9 PASS、6.2 PASS（资本化 4.0%），快筛阻断逻辑不受影响；
- 文件系统/解析异常（`--pdf` 传目录、无权限、损坏 PDF）统一降级为 `deep.status=error`，
  保留快筛结果，不中断 analyze。

## 8. 开放问题

1. **规则来源已内联**：后续本地维护，变更需同步 `docs/reference/` 与 `SOURCE_VERSION`；
   若上游 GitHub 仍有提交，用 `git diff 483d3fc..HEAD -- skill/references/checklist-rules.md`
   人工比对决定是否跟进；
2. S2 的"一年内到期非流动负债"已改用真实字段 `non_cur_liab_due_1y`；
   **已知盲区**：分子只计 money_cap，不含交易性金融资产（理财）——持有大量理财的
   公司（双汇型）短债覆盖被低估；
3. 深度模式的判断型规则已做勾选项/按句解析（5.4 ≥2 处独董辞职 → FAIL），但仍是
   启发式；5.3"多次变更"、5.6"多次跨行业收购"仍保守给 WARN（单本年报无法可靠证明
   跨年多次）；是否引入 LLM 深度复核（与 04 号模块的 `pending` 检查清单同思路）待定；
   扫描版 PDF 无 OCR 能力，明确降级 `error`；
4. 3.5 坏账计提同行对比已实现（2026-10-19 B 步）：同行 `income`/`balancesheet`
   不支持多代码批量，按市值前 8 家单票查询并缓存（冷启动约 5–6 秒）；同行样本
   不足 3 家、应收账款 <总资产 1% 或 `credit_impa_loss` 缺失时 SKIP；同行中位数
   本身受行业结构影响，后续可扩样校准阈值；
5. 消费类公司敏感度校准（2026-10 双汇 27 分 blocked 复盘）：1.6 减值占利润 5% 阈值
   对存货跌价常态化的食品公司偏严；4.3 资产膨胀规则对高分红+理财模式公司偏严——
   两项维持上游原判（有 `--force` 通道），跑数扩样后再定是否单独校准；
6. 5.8 其他应收款的小额豁免边界（2026-10 美的 0.5%/YoY54% 触发 FAIL）待业务校准：
   是否把 yoy 分支的 FAIL 门槛从"占比>0.5%"提高到 1%，或改为仅 WARN。
