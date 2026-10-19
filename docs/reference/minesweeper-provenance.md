# financial-report-minesweeper 并入记录（Provenance）

> 迁移日期：2026-10-18
> 源仓库：https://github.com/terancejiang/financial-report-minesweeper
> 版本锚点：`main` @ `483d3fc`（2026-04-16，最后一次提交）；规则文件最后变更提交为 `77d2811`（2026-04-12，v1.1 增加 Rule 5.8/5.9）
> 状态：**上游本地副本已删除**；本工程是规则的唯一事实源。

## 为什么并入

原 `financial-report-minesweeper` 是唐朝《手把手教你读财报》方法论的 Claude Code Skill
实现（LLM 读规则清单 + 年报 PDF 逐条判断）。本工程的排雷层（02 号文档）一直引用其规则
定义，但两套工程并存导致：

1. 规则引用链跨目录、版本锚点难以对齐（此前 `SOURCE_VERSION` 写的 `2026-06` 与上游
   git 历史不符）；
2. 上游的 PDF 深度模式能力（下载器、搜索策略、规则判定细节）无法直接为本工程的
   Python pipeline 复用；
3. 规则依据、读书笔记等知识资产存在丢失风险。

因此将仍有价值的内容内联到本工程，随后删除上游本地副本。

## 已内联的资产

| 上游资产 | 本地落点 | 处理方式 |
|---------|---------|---------|
| `skill/references/checklist-rules.md` | [minesweeper-checklist-rules.md](minesweeper-checklist-rules.md) | 全文快照 + provenance 头（规则计数以本地编号为准：32 条） |
| `README.md`「排雷规则体系」章节（含每条原书依据） | [minesweeper-rule-rationale.md](minesweeper-rule-rationale.md) | 抽取规则依据段落，剔除安装说明 |
| `SKILL.md` Phase 2 + `download-report.md` | [pdf-deep-mode-guide.md](pdf-deep-mode-guide.md) | 改写为 Python 深度模式操作指引（含关键词映射） |
| `scripts/download_report.py` | `src/sqa/minesweeper/deep/pdf_download.py` | 库化改造 + 巨潮 A 股搜索（参考龟龟投研 MIT 实现） |
| `手把手读财报/` 8 章读书笔记 | `docs/knowledge/手把手读财报/` | 原样迁移 + Obsidian wikilink 转相对链接；内部学习用 |
| 数据层健壮性（重试/退避） | `src/sqa/data/tushare_client.py` | 吸收重试逻辑 |
| tushare `yc_cb` 无风险利率 | `src/sqa/config.py` | 新增 tushare 后端（优先于 akshare） |

## 未内联的资产（有意放弃）

- **港美股支持**（yfinance 集成、US/HK 三表字段映射、港股 AES 下载）：本工程仅做 A 股；
- **龟龟投研框架派生指标**（factor2/3/4、SOTP、EV baseline 等）：属于另一套投研框架；
- **install.sh / Claude Code 安装流程 / book-cover 图片**：与 pip 包 + CLI 形态无关；
- 上游 README 的安装/使用章节：仅保留规则依据部分。

## 规则计数与口径

上游文档存在历史口径漂移（README 写“7 层 30 条”、SKILL/checklist 写“28 条”、
CHANGELOG 写“28→30”）。按规则文件实际编号，规则共 **32 条**：

| 层 | 条数 | 编号 |
|----|-----:|------|
| L0 门槛 | 2 | 0.1–0.2 |
| L1 利润表 | 6 | 1.1–1.6 |
| L2 现金流 | 3 | 2.1–2.3 |
| L3 资产负债表 | 5 | 3.1–3.5 |
| L4 交叉验证 | 5 | 4.1–4.5 |
| L5 非财务 | 9 | 5.1–5.9 |
| L6 行业 | 2 | 6.1–6.2 |

“30 条”通常指不含 L0 两道门槛的计数，“28 条”为 v1.0 口径。本工程另补 Layer S 4 条
（S1–S4），规则槽位共 **36 条**；快筛执行 **29 条**（25 条上游 + 4 条 S；3.5 依赖同行
坏账数据，样本不足时 SKIP），深度模式补齐 7 条 PDF 规则 + 5.8 补充检查。

## 许可与使用限制

- 上游仓库**未声明 LICENSE**（本地与 GitHub 均无 LICENSE 文件）。本项目与上游为同一
  工作区/同一维护者，内联属内部整合；若未来对外发布本工程，需补充来源声明并复核授权。
- `docs/knowledge/手把手读财报/` 是《手把手教你读财报》的读书笔记，含原书引用，
  **仅限内部学习使用，请勿对外发布或提交到公开仓库**。
- `pdf_download.py` 的巨潮搜索模式参考龟龟投研（https://github.com/TianHades/Turtle_investment，
  MIT 许可）的 A 股实现并简化，已在文件 docstring 中注明。

## 后续规则版本管理

- `src/sqa/minesweeper/engine.py` 的 `SOURCE_VERSION` 使用 **commit hash**（而非日期）作锚点；
- 上游仓库若仍有维护，变更时以 `git diff 483d3fc..HEAD -- skill/references/checklist-rules.md`
  为基准人工确认是否跟进；
- 本地规则变更需同步更新 `docs/reference/minesweeper-checklist-rules.md` 与 02 号文档。
