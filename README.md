# StockQualityAnalysis · A股股票质地分析

对初选股票做质地分析：**分类判定 → 排雷 → 质量评分 → 专属模块 → 估值**。
规则设计见 `docs/`（01-分类、02-排雷集成、03-质量权重、04-专属模块、05-估值），
排雷规则全文与原书依据见 `docs/reference/`。

## 快速开始

```bash
pip install -e ".[dev,pdf]"   # pdf 可选依赖用于深度模式（pypdf；有 pdftotext 时可省）
cp .env.example .env          # 填入 TUSHARE_TOKEN（https://tushare.pro 注册）

sqa analyze 600519             # 完整质地分析：分类→排雷→评分→模块→估值（一条命令出报告）
sqa classify 600519            # 仅分类判定（主风格/红利/行业标签）
sqa minesweeper 600519         # 仅财报排雷（快筛：纯 tushare 结构化数据）
sqa minesweeper 600519 --mode deep      # 深度模式：下载/解析年报 PDF，补 PDF 类规则
sqa analyze 600519 --deep               # 排雷环节使用深度模式
sqa golden                     # 分类金标准测试集验收
```

深度模式支持 `--pdf <本地年报PDF>` 或 `--pdf-url <巨潮/雪球PDF链接>` 跳过自动搜索；
PDF 获取/解析失败时自动降级为快筛结果并记录原因（不会中断分析）。
详见 [docs/reference/pdf-deep-mode-guide.md](docs/reference/pdf-deep-mode-guide.md)。

## 测试与验收

```bash
pytest                                    # 离线单元测试（规则纯函数 + 深度模式抽取/判定）
SQA_INTEGRATION=1 pytest tests/test_golden_set.py   # 集成：真实数据金标准
python -m sqa.cli golden                  # 金标准 13/13（需 TUSHARE_TOKEN / .env）
```

一键验收（Windows PowerShell 5.1 / 7）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/acceptance.ps1 -Offline  # 离线
powershell -ExecutionPolicy Bypass -File scripts/acceptance.ps1            # 额外 golden
powershell -ExecutionPolicy Bypass -File scripts/acceptance.ps1 -Full      # 额外深度/force smoke
# PowerShell 7 可直接：pwsh -File scripts/acceptance.ps1 -Full
```

CI 见 `.github/workflows/ci.yml`：push/PR 跑离线测试、ruff、compileall；`workflow_dispatch`
手动跑 golden（需仓库 secret `TUSHARE_TOKEN`）。

## 维护

- **缓存**：`data_cache/` 全部可重建；tushare 缓存键包含接口参数（含显式 fields），
  字段口径升级会自然生成新缓存；深度模式 PDF 文本缓存可整体删除后重取；
- **旧报告**：`python scripts/archive_reports.py --dry-run` 预览，默认把
  `reports/analyze_*.json` 中早于当天的报告移入 `reports/archive_pre_<日期>/`；
- **首次建仓（手动）**：`git init` → 检查 `git status`（不应出现 `.env` / `data_cache/` /
  `reports/` / `*.pdf`）→ `git add .` → `git commit -m "sqa v0.1.0 baseline"` →
  `git tag v0.1.0`。

## 结构

```
docs/                 规则文档（单一事实源）
  01-05*.md           分类/排雷/评分/模块/估值设计
  reference/          排雷规则全文、原书依据、深度模式指引、来源记录（provenance）
  knowledge/          读书笔记知识库（内部学习用，勿外发）
config/thresholds.yaml  全部可调阈值
scripts/              验收脚本与旧报告归档
.github/workflows/    CI（离线测试 + 手动 golden）
CHANGELOG.md           版本变更记录
src/sqa/
  classify/           分类规则（纯函数，可离线测试）
  minesweeper/        排雷规则与引擎（L0-L6 + 补充层 S）
    deep/             深度模式：PDF 获取/文本抽取/证据抽取/PDF 规则
  quality/            质量评分（六维度 × 权重表）
  modules/            专属模块（周期位置/分红持续性/成长质量/护城河）
  valuation/          估值（PB/PE 分位、PEG、周期双验证、股息利差）
  data/               tushare 客户端（缓存+限速+重试）与取数器
  golden.py           金标准测试集
  cli.py              命令行入口
data_cache/           API 缓存 + 年报 PDF/文本缓存（可整目录删除重取）
reports/              运行结果
```

## 规则来源

排雷规则源自兄弟项目 `financial-report-minesweeper`（唐朝《手把手教你读财报》方法论的
Claude Code Skill 实现）。2026-10-18 已将其规则全文、原书依据、深度模式 SOP 与读书笔记
内联到本工程，并删除上游本地副本（GitHub 仓库仍保留，版本锚点 `main @ 483d3fc`）。
迁移清单、许可说明与规则计数口径见
[docs/reference/minesweeper-provenance.md](docs/reference/minesweeper-provenance.md)。

## 数据口径

- 年报数据（end_date 以 1231 结尾），同一报告期按公告日取最新；
- 排雷/质量取数使用显式 tushare `fields` 清单（`src/sqa/data/fields.py`），
  可选字段（`inv_turn`、`credit_impa_loss` 等）显式请求，避免默认列缺字段导致规则静默 SKIP；
- 分红：只取 `div_proc=实施`，金额用税前 `cash_div_tax`，同一报告期同额记录去重，
  中期分红并入所属财年；
- CAGR 统一为近 3 年（4 个年度点，任一点缺失降级 SKIP）；非周期盈利指标取 3 年均值，
  周期股取 10 年中位数；
- 金额单位元；股息率 %；PE/PB 分位为近 5 年时序分位；
- 同行锚点：同行业年报中位数（质量评分取前 60 家；排雷 3.5 坏账取前 8 家，
  单票查询并缓存）；
- 数据缺失一律降级 SKIP 并在 evidence 中标注，绝不猜测补数；
- 深度模式的证据片段（页码+原文）随报告一并输出，供人工复核。
