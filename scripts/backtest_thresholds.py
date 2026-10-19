#!/usr/bin/env python
"""D 步扩样回测 / 阈值敏感性分析（可重跑）。

用法：
    python scripts/backtest_thresholds.py
    python scripts/backtest_thresholds.py --universe config/backtest_universe.txt
    python scripts/backtest_thresholds.py --universe config/backtest_universe_100.txt --label d_backtest100

输出：
    reports/d_backtest_YYYYMMDD.json   逐股规则结果 + 阈值敏感性
    reports/d_backtest_YYYYMMDD.md     汇总报告

说明：这是"方向性校准"样本（30 只，覆盖消费/周期/科技/价值），不是全市场
回测；所有数据走 tushare 缓存，首次运行可能较慢。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from sqa.classify.exclusions import exclusion_reason
from sqa.config import PROJECT_ROOT, load_thresholds
from sqa.data.fetchers import fetch_basic, resolve_ts_code
from sqa.data.tushare_client import SqaDataError, TushareClient
from sqa.minesweeper.engine import run_minesweeper
from sqa.minesweeper.fetch import fetch_annual_full, fetch_audit
from sqa.minesweeper.rules_nonfinancial import _normalize_agency


def _num(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _impair(row: dict) -> float | None:
    a = _num(row, "assets_impair_loss", "asset_impair_loss")
    c = _num(row, "credit_impa_loss", "credit_impair_loss")
    if a is None and c is None:
        return None
    return abs(a or 0.0) + abs(c or 0.0)


def sensitivity_1_6(income: dict[int, dict]) -> dict:
    years = sorted(income)
    if len(years) < 2:
        return {"status": "skip"}
    cur, prev = _impair(income[years[-1]]), _impair(income[years[-2]])
    np_cur = _num(income[years[-1]], "n_income_attr_p")
    if cur is None or prev is None:
        return {"status": "skip", "reason": "impairment missing"}
    yoy = cur / prev - 1.0 if prev > 0 else None
    to_profit = cur / abs(np_cur) * 100 if np_cur else None
    out = {"yoy": yoy, "to_profit": to_profit, "verdicts": {}}
    # 与 D 步校准后的规则一致：profit>thr，或 yoy>100% 且 profit>5%
    for threshold in (5.0, 8.0, 10.0, 15.0):
        if to_profit is not None:
            if to_profit > threshold or (yoy is not None and yoy > 1.0 and to_profit > 5):
                verdict = "FAIL"
            elif to_profit > 5 or (yoy is not None and yoy > 0.5 and to_profit > 1):
                verdict = "WARN"
            else:
                verdict = "PASS"
        elif (yoy is not None and yoy > 1.0):
            verdict = "FAIL"
        elif (yoy is not None and yoy > 0.5):
            verdict = "WARN"
        else:
            verdict = "PASS"
        out["verdicts"][str(threshold)] = verdict
    return out


def _asset_streak(total_assets: list[float | None], revenue: list[float | None],
                  np_: list[float | None], multiplier: float) -> int:
    best = 0
    streak = 0
    for i in range(1, len(total_assets)):
        a, a_prev = total_assets[i], total_assets[i - 1]
        r, r_prev = revenue[i] if i < len(revenue) else None, \
            revenue[i - 1] if i - 1 < len(revenue) else None
        p, p_prev = np_[i] if i < len(np_) else None, \
            np_[i - 1] if i - 1 < len(np_) else None
        if None in (a, a_prev, r, r_prev) or a_prev <= 0 or r_prev <= 0:
            streak = 0
            continue
        a_g, r_g = a / a_prev - 1.0, r / r_prev - 1.0
        p_g = p / p_prev - 1.0 if p is not None and p_prev and p_prev > 0 else None
        if r_g <= 0:
            streak = 0
            continue
        if a_g > r_g * multiplier and p_g is not None and p_g > 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def sensitivity_4_3(income: dict[int, dict], balance: dict[int, dict]) -> dict:
    years = sorted(set(income) | set(balance))
    ta = [balance.get(y, {}).get("total_assets") for y in years]
    rev = [income.get(y, {}).get("revenue") for y in years]
    np_ = [income.get(y, {}).get("n_income_attr_p") for y in years]
    if len(years) < 3:
        return {"status": "skip"}
    out = {"max_streak": {}, "verdicts": {}}
    for mult in (2.0, 2.5, 3.0, 3.5):
        streak = _asset_streak(ta, rev, np_, mult)
        out["max_streak"][str(mult)] = streak
        out["verdicts"][str(mult)] = "FAIL" if streak >= 2 else "PASS"
    return out


def sensitivity_5_1(audit_rows: list[dict]) -> dict:
    if not audit_rows:
        return {"status": "skip"}
    rows = sorted(audit_rows, key=lambda r: str(r.get("end_date") or ""))
    out = {"agencies": {}, "changes": {}, "verdicts": {}}
    for window in (3, 5, 10):
        agencies = []
        for row in rows[-window:]:
            agency = _normalize_agency(str(row.get("audit_agency") or ""))
            if agency and (not agencies or agencies[-1] != agency):
                agencies.append(agency)
        changes = max(0, len(agencies) - 1)
        out["agencies"][str(window)] = len(agencies)
        out["changes"][str(window)] = changes
        out["verdicts"][str(window)] = ("FAIL" if changes >= 2
                                        else "WARN" if changes == 1 else "PASS")
    return out


def _rule_2_3(balance: dict, rate: float | None,
              base_rate: float, materiality: float) -> str:
    cash = _num(balance, "money_cap")
    if cash is None:
        return "SKIP"
    ibd = sum(_num(balance, f) or 0.0 for f in
              ("st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"))
    ta = _num(balance, "total_assets")
    if ibd <= 0:
        return "PASS"
    if ta and ibd / ta < materiality:
        return "PASS"
    if rate is None:
        return "SKIP"
    implied = abs(rate) / ibd * 100
    if cash > ibd and implied > base_rate + 4:
        return "FAIL"
    if cash > ibd * 0.5 and implied > base_rate + 2:
        return "WARN"
    return "PASS"


def sensitivity_2_3(income: dict[int, dict], balance: dict[int, dict],
                    base_rate: float) -> dict:
    if not balance:
        return {"status": "skip"}
    latest_year = max(income) if income else max(balance)
    latest = balance[latest_year]
    latest_income = income.get(latest_year, {})
    fin_exp = _num(latest_income, "fin_exp")
    int_exp = _num(latest_income, "int_exp")
    # 与生产代码一致：优先 int_exp；缺失时仅在 fin_exp 为正（费用）时回退
    production_rate = int_exp if int_exp is not None else (
        fin_exp if fin_exp is not None and fin_exp > 0 else None)
    out = {"fin_exp": fin_exp, "int_exp": int_exp, "verdicts": {}}
    for materiality in (0.005, 0.01, 0.02):
        for label, rate in (("fin", fin_exp), ("int", production_rate)):
            key = f"{label}:{materiality:.3f}"
            out["verdicts"][key] = _rule_2_3(latest, rate, base_rate, materiality)
    return out


def _summary_markdown(payload: dict) -> str:
    stocks = payload["stocks"]
    lines = ["# 扩样回测 / 阈值敏感性报告", "",
             f"- 样本数：{len(stocks)}（其中成功 {sum(1 for s in stocks if s.get('risk'))}）",
             f"- 股票池：{payload['universe']}",
             f"- 生成时间：{payload['generated']}", ""]
    lines += ["## 1. 风险分布", ""]
    dist = payload["risk_distribution"]
    lines += [f"- {k}: {v}" for k, v in dist.items()]
    lines += ["", "## 2. 规则触发率（WARN/FAIL）", "",
              "| 规则 | WARN | FAIL | SKIP |", "|---|---:|---:|---:|"]
    for rule, counts in payload["rule_counts"].items():
        lines.append(f"| {rule} | {counts.get('WARN', 0)} | {counts.get('FAIL', 0)} | {counts.get('SKIP', 0)} |")
    lines += ["", "## 3. 阈值敏感性", ""]
    lines.append(f"- 1.6 减值/利润阈值：{json.dumps(payload['sensitivity']['1.6']['verdict_counts'], ensure_ascii=False)}")
    lines.append(f"- 4.3 资产增速倍数：{json.dumps(payload['sensitivity']['4.3']['verdict_counts'], ensure_ascii=False)}")
    lines.append(f"- 5.1 审计窗口：{json.dumps(payload['sensitivity']['5.1']['verdict_counts'], ensure_ascii=False)}")
    lines.append(f"- 2.3 财务费用/重要性：{json.dumps(payload['sensitivity']['2.3']['verdict_counts'], ensure_ascii=False)}")
    lines += ["", "## 4. 样本明细", "",
              "| 代码 | 名称 | 行业 | 风险 | 得分 | FAIL/WARN |", "|---|---|---|---|---:|---|"]
    for s in stocks:
        risk = s.get("risk") or {}
        lines.append(f"| {s['symbol']} | {s.get('name','')} | {s.get('industry','')} | "
                     f"{risk.get('verdict','ERROR')} | {risk.get('total_score','')} | "
                     f"{','.join(risk.get('flagged', []))} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="扩样回测/阈值敏感性")
    parser.add_argument("--universe", default=str(PROJECT_ROOT / "config" / "backtest_universe.txt"))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument("--label", default="d_backtest",
                        help="输出文件名前缀（默认 d_backtest）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    args = parser.parse_args()

    cfg = load_thresholds()
    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))
    codes = [line.strip() for line in Path(args.universe).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if args.limit:
        codes = codes[:args.limit]
    stocks: list[dict] = []
    rule_counts: dict[str, dict[str, int]] = {}
    risk_distribution: dict[str, int] = {}

    for code in codes:
        ts_code = resolve_ts_code(code)
        try:
            basic = fetch_basic(client, ts_code)
            excluded = exclusion_reason(basic.name, basic.industry)
            if excluded:
                stocks.append({"symbol": code, "name": basic.name,
                               "industry": basic.industry, "excluded": excluded})
                print(f"{code} {basic.name} EXCLUDED {excluded}")
                continue
            result = run_minesweeper(code, cfg=cfg, client=client)
            income = fetch_annual_full(client, "income", ts_code, cfg.history_years)
            balance = fetch_annual_full(client, "balancesheet", ts_code, cfg.history_years)
            audit = fetch_audit(client, ts_code)
        except SqaDataError as exc:
            stocks.append({"symbol": code, "error": str(exc)})
            print(f"{code} ERROR {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - 单票异常不中断整轮回测
            stocks.append({"symbol": code, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{code} ERROR {type(exc).__name__}: {exc}")
            continue

        rules = {r["id"]: {"verdict": r["verdict"], "detail": r["detail"]}
                 for r in result["rules"]}
        for rule_id, rule in rules.items():
            counts = rule_counts.setdefault(rule_id, {"WARN": 0, "FAIL": 0, "SKIP": 0})
            counts[rule["verdict"]] = counts.get(rule["verdict"], 0) + 1
        risk_distribution[result["verdict"]] = risk_distribution.get(result["verdict"], 0) + 1
        stock = {
            "symbol": code, "name": basic.name, "industry": basic.industry,
            "risk": {
                "verdict": result["verdict"], "risk_level": result["risk_level"],
                "total_score": result["total_score"], "base_score": result["base_score"],
                "combo_bonus": result["combo_bonus"], "forceable": result["forceable"],
                "flagged": [f"{rid}:{r['verdict']}" for rid, r in rules.items()
                            if r["verdict"] in ("WARN", "FAIL")],
            },
            "rules": rules,
            "sensitivity": {
                "1.6": sensitivity_1_6(income),
                "4.3": sensitivity_4_3(income, balance),
                "5.1": sensitivity_5_1(audit),
                "2.3": sensitivity_2_3(income, balance, cfg.ms_base_rate),
            },
        }
        stocks.append(stock)
        print(f"{code} {basic.name} {result['verdict']} {result['total_score']}")

    sens = {
        "1.6": {"verdict_counts": {str(t): {"FAIL": 0, "WARN": 0, "PASS": 0, "SKIP": 0}
                                    for t in (5.0, 8.0, 10.0, 15.0)}},
        "4.3": {"verdict_counts": {m: {"FAIL": 0, "PASS": 0, "SKIP": 0}
                                    for m in ("2.0", "2.5", "3.0", "3.5")}},
        "5.1": {"verdict_counts": {w: {"FAIL": 0, "WARN": 0, "PASS": 0, "SKIP": 0}
                                    for w in ("3", "5", "10")}},
        "2.3": {"verdict_counts": {}},
    }
    for stock in stocks:
        if "sensitivity" not in stock:
            continue
        s16 = stock["sensitivity"]["1.6"]
        if s16.get("status") != "skip":
            for threshold, verdict in s16["verdicts"].items():
                sens["1.6"]["verdict_counts"][threshold][verdict] += 1
        s43 = stock["sensitivity"]["4.3"]
        if s43.get("status") != "skip":
            for mult, verdict in s43["verdicts"].items():
                sens["4.3"]["verdict_counts"][mult][verdict] += 1
        s51 = stock["sensitivity"]["5.1"]
        if s51.get("status") != "skip":
            for window, verdict in s51["verdicts"].items():
                sens["5.1"]["verdict_counts"][window][verdict] += 1
        s23 = stock["sensitivity"]["2.3"]
        if s23.get("status") != "skip":
            for key, verdict in s23["verdicts"].items():
                counts = sens["2.3"]["verdict_counts"].setdefault(
                    key, {"FAIL": 0, "WARN": 0, "PASS": 0, "SKIP": 0})
                counts[verdict] += 1

    payload = {"generated": datetime.now().isoformat(timespec="seconds"),
               "universe": str(args.universe), "stocks": stocks,
               "rule_counts": dict(sorted(rule_counts.items())),
               "risk_distribution": risk_distribution,
               "sensitivity": sens}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"{args.label}_{stamp}.json"
    md_path = out_dir / f"{args.label}_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_summary_markdown(payload), encoding="utf-8")
    print(f"\nwritten {json_path}\nwritten {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
