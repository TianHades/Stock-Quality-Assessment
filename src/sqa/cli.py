"""CLI 入口。

用法:
    sqa classify 600519            # 单只判定（人类可读摘要）
    sqa classify 600519 --json     # 完整 JSON 输出
    sqa golden                     # 跑金标准测试集（14→13 只）
    sqa golden --refresh           # 忽略缓存强制刷新数据
"""
from __future__ import annotations

import argparse
import json
import sys

from sqa import __version__
from sqa.classify.pipeline import classify_stock
from sqa.data.tushare_client import SqaDataError
from sqa.golden import run_golden


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK，强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="sqa", description="股票质地分析：分类判定器")
    parser.add_argument("--version", action="version", version=f"sqa {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_classify = sub.add_parser("classify", help="单只股票分类判定")
    p_classify.add_argument("symbol", help="6位A股代码，如 600519")
    p_classify.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_classify.add_argument("--refresh", action="store_true", help="强制刷新缓存")

    p_golden = sub.add_parser("golden", help="跑 01 号文档金标准测试集")
    p_golden.add_argument("--refresh", action="store_true", help="强制刷新缓存")

    p_ms = sub.add_parser("minesweeper", help="财报排雷（02 号文档规则）")
    p_ms.add_argument("symbol", help="6位A股代码")
    p_ms.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_ms.add_argument("--refresh", action="store_true", help="强制刷新缓存")
    p_ms.add_argument("--mode", choices=["quick", "deep"], default="quick",
                      help="快筛（默认，纯结构化数据）/ 深度（年报 PDF 规则）")
    p_ms.add_argument("--pdf", help="深度模式：指定本地年报 PDF 路径（跳过自动下载）")
    p_ms.add_argument("--pdf-url", help="深度模式：指定年报 PDF URL（跳过自动搜索）")

    p_an = sub.add_parser("analyze", help="完整质地分析：分类→排雷→评分")
    p_an.add_argument("symbol", help="6位A股代码")
    p_an.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_an.add_argument("--refresh", action="store_true", help="强制刷新缓存")
    p_an.add_argument("--force", action="store_true", help="排雷高风险时强制继续（评级双降）")
    p_an.add_argument("--deep", action="store_true",
                      help="排雷使用深度模式（下载/解析年报 PDF，补 PDF 类规则）")
    p_an.add_argument("--pdf", help="深度模式：指定本地年报 PDF 路径")
    p_an.add_argument("--pdf-url", help="深度模式：指定年报 PDF URL")

    args = parser.parse_args(argv)

    # PDF 参数与深度模式联动，避免"传了 --pdf 却仍跑快筛"的静默误解
    if args.cmd == "minesweeper" and args.mode != "deep" and (args.pdf or args.pdf_url):
        parser.error("--pdf/--pdf-url 需要配合 --mode deep 使用")
    if args.cmd == "analyze" and not args.deep and (args.pdf or args.pdf_url):
        parser.error("--pdf/--pdf-url 需要配合 --deep 使用")

    if args.cmd == "classify":
        try:
            result = classify_stock(args.symbol, refresh=args.refresh)
        except SqaDataError as exc:
            return _data_error(exc)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_summary(result)
        return 0

    if args.cmd == "golden":
        return _run_golden_cli(refresh=args.refresh)

    if args.cmd == "minesweeper":
        from sqa.minesweeper.engine import run_minesweeper

        try:
            result = run_minesweeper(args.symbol, refresh=args.refresh, mode=args.mode,
                                     pdf_path=args.pdf, pdf_url=args.pdf_url)
        except SqaDataError as exc:
            return _data_error(exc)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_ms_summary(result)
        return 0

    if args.cmd == "analyze":
        from sqa.analyze import run_analyze

        try:
            result = run_analyze(args.symbol, refresh=args.refresh, force=args.force,
                                 deep=args.deep, pdf_path=args.pdf,
                                 pdf_url=args.pdf_url)
        except SqaDataError as exc:
            return _data_error(exc)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_analyze_summary(result)
        return 0

    return 1  # pragma: no cover


def _data_error(exc: Exception) -> int:
    print(f"数据获取失败: {exc}", file=sys.stderr)
    return 2


def _print_deep(deep: dict | None) -> None:
    """深度模式摘要（PDF 来源、页数与 PDF 规则判定）。"""
    if not deep:
        return
    pdf = deep.get("pdf") or {}
    if deep.get("status") == "ok":
        print(f"深度  : 年报 PDF {pdf.get('pages', 0)} 页（{pdf.get('backend', '')}，"
              f"来源 {pdf.get('source', '')}）")
        verdicts = deep.get("verdicts") or {}
        if verdicts:
            print("  └ PDF 规则: " + " / ".join(f"{k} {v}" for k, v in verdicts.items()))
    else:
        label = {"skipped": "跳过", "error": "失败"}.get(deep.get("status"), deep.get("status"))
        print(f"深度  : {label} — {deep.get('message', '')}")


def _print_ms_summary(r: dict) -> None:
    print(f"{r['name']} ({r['symbol']})  模式: {r['mode']}")
    print(f"  闸门   : {r['verdict']}   风险等级: {r['risk_level']}"
          f"   总分: {r['total_score']}（层分 {r['base_score']} + 组合 {r['combo_bonus']}）")
    if r["combos"]:
        for c in r["combos"]:
            print(f"  ⚡ 组合信号: {c}")
    _print_deep(r.get("deep"))
    flagged = [x for x in r["rules"]
               if x["verdict"] in ("WARN", "FAIL") or x["verdict"] == "SKIP"
               and not x["detail"].startswith("深度")]
    for x in flagged:
        mark = "⚠" if x["verdict"] == "WARN" else "✗"
        if x["verdict"] == "FAIL":
            print(f"  {mark} FAIL [{x['layer']} {x['id']}] {x['detail']}")
        elif x["verdict"] == "WARN":
            print(f"  {mark} WARN [{x['layer']} {x['id']}] {x['detail']}")
    print(f"  （{sum(1 for x in r['rules'] if x['verdict'] == 'SKIP')} 条 SKIP，详见 --json）")


_DIM_CN = {
    "profitability": "盈利能力", "growth": "成长性", "cashflow": "现金流质量",
    "health": "财务健康", "efficiency": "运营效率", "governance": "治理与回报",
}


def _print_analyze_summary(r: dict) -> None:
    print(f"━━━ {r['name']} ({r['symbol']}) 质地分析 ━━━")
    cls = r["classification"]
    print(f"分类: {cls['style']} + {list(cls['badges']) or '无'}标签 / 行业 {cls['industry_tag']}"
          f"（置信度 {cls['confidence']}）")
    for w in cls["warnings"]:
        print(f"  ⚠ {w}")

    ms = r.get("minesweeper")
    if ms:
        print(f"排雷: {ms['verdict']}（{ms['risk_level']}，{ms['total_score']} 分）")
        _print_deep(ms.get("deep"))

    if r["pipeline"] != "complete":
        print(f"流水线终止: {r.get('stop_reason')}")
        return

    q = r["quality"]
    grade_str = q["grade"] or "数据不足"
    if q["grade_adjusted"] and q["grade_adjusted"] != q["grade"]:
        grade_str += f" → {q['grade_adjusted']}（排雷联动降档）"
    print(f"质分: {q['quality_score']}  评级: {grade_str}  完整度: {q['data_completeness']:.0%}")
    print("维度得分（权重）:")
    for dim, d in q["dimensions"].items():
        if d.get("score") is None:
            print(f"  {_DIM_CN[dim]:<8} SKIP（{d.get('note', '')}）")
            continue
        bar = "█" * int(d["score"] // 10)
        print(f"  {_DIM_CN[dim]:<8} {d['score']:>5.1f}（{d['weight']:.0f}%）{bar}")

    for name, m in (r.get("modules") or {}).items():
        if m.get("error"):
            print(f"模块 {name}: 执行失败（{m['error']}）")
            continue
        if name == "cycle_position":
            print(f"周期位置: {m.get('position_cn')}（置信度 {m.get('confidence')}）")
            print(f"  └ {m.get('quality_advice')}")
        elif name == "dividend_sustainability":
            print(f"分红持续: {m.get('verdict')}")
            for w in m.get("warnings", []):
                print(f"  ⚠ {w}")
        elif name == "growth_quality_check":
            print(f"成长质检: {m.get('verdict')}（{m.get('score')} 分）")
            for n in m.get("notes", []):
                print(f"  ⚠ {n}")
        elif name == "moat_check":
            print(f"护城河  : {m.get('verdict')}（{m.get('score')} 分）")
            if m.get("erosion_warning"):
                print(f"  ⚠ {m['erosion_warning']}")

    v = r.get("valuation")
    if v:
        verdict_cn = {"undervalued": "低估", "fair": "合理", "overvalued": "高估",
                      "NA": "数据不足"}.get(v["verdict"], v["verdict"])
        print(f"估值    : {verdict_cn}（{'+'.join(v['methods_applied'])}）")
        m = v["metrics"]
        parts = []
        if m.get("pb_pctl") is not None:
            parts.append(f"PB分位 {m['pb_pctl']:.0%}")
        if m.get("pe_pctl") is not None and "pe_percentile" in v["methods_applied"]:
            parts.append(f"PE分位 {m['pe_pctl']:.0%}")
        if m.get("peg") is not None:
            parts.append(f"PEG {m['peg']}")
        if m.get("div_spread_pp") is not None:
            parts.append(f"股息利差 {m['div_spread_pp']:.1f}pp")
        if parts:
            print(f"  └ {' / '.join(parts)}")
        for s in v.get("signals", []):
            print(f"  ★ {s}")
        for n in v.get("notes", []):
            print(f"  · {n}")
    print("报告已保存至 reports/（--json 查看全部指标明细与检查清单）")


def _print_summary(result: dict) -> None:
    print(f"{result['name']} ({result['symbol']})")
    print(f"  主风格   : {result['style']}   置信度: {result['confidence']}")
    print(f"  附加标签 : {list(result['badges']) or '—'}")
    print(f"  行业标签 : {result['industry_tag']}")
    if result["signals"]:
        print(f"  信号     : {list(result['signals'])}")
    for w in result["warnings"]:
        print(f"  ⚠ 警示   : {w}")
    print(f"  模块清单 : {list(result['modules']) or '—'}")


def _run_golden_cli(refresh: bool) -> int:
    results = run_golden(refresh=refresh)
    header = f"{'代码':<8}{'名称':<6}{'主风格':<16}{'红利':<6}{'行业':<10}{'结果':<4}"
    print(header)
    print("-" * 56)
    n_pass = 0
    for r in results:
        style_str = r["actual_style"] or "?"
        mark = "✓" if r["pass"] else "✗"
        n_pass += 1 if r["pass"] else 0
        print(f"{r['symbol']:<8}{r['name']:<6}{style_str:<16}"
              f"{'红利' if 'dividend' in (r['actual_badges'] or []) else '—':<6}"
              f"{r['actual_tag'] or '?':<10}{mark:<4}")
        if not r["pass"]:
            _print_mismatch(r)
    total = len(results)
    print("-" * 56)
    print(f"通过 {n_pass}/{total}")
    return 0 if n_pass == total else 1


def _print_mismatch(r: dict) -> None:
    if not r["style_ok"]:
        print(f"    ✗ 主风格: 预期 {r['expected_style']} 实际 {r['actual_style']}")
    if not r["badges_ok"]:
        print(f"    ✗ 红利: 预期 {r['expected_badges']} 实际 {r['actual_badges']}")
    if not r["tag_ok"]:
        print(f"    ✗ 行业: 预期 {r['expected_tag']} 实际 {r['actual_tag']}")
    if r["actual_style"] == "ERROR":
        print(f"    ✗ 错误: {r['detail'].get('error')}")


if __name__ == "__main__":
    sys.exit(main())
