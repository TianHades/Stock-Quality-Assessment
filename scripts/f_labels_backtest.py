#!/usr/bin/env python
"""F 步：历史时点风险快照 + 未来收益标签回测（可重跑）。

做法：
1. 选一个历史形成日（默认 2023-04-30），用 `run_minesweeper(as_of=...)` 只取
   当日之前披露的年报/审计记录，避免未来函数；质押/股东/同行等当前快照规则 SKIP。
2. 取形成日后的 1 年 / 2 年复权收益（daily × adj_factor）。
3. 取 namechange 中形成日后的 ST 标记（样本当前均为上市状态，存在幸存者偏差）。
4. 比较风险分/verdict 与未来收益、规则触发与收益的相关性。

这是小样本方向性验证，不是严格可交易回测；不处理交易成本、停牌、退市幸存者偏差。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sqa.config import PROJECT_ROOT, load_thresholds
from sqa.data.fetchers import resolve_ts_code
from sqa.data.tushare_client import SqaDataError, TushareClient
from sqa.minesweeper.engine import run_minesweeper


def _last_close_before(closes: dict[str, float], target: str) -> float | None:
    dates = [d for d in closes if d <= target]
    return closes[max(dates)] if dates else None


def _future_return(client: TushareClient, ts_code: str, formation: str,
                   horizon: str) -> dict:
    end = horizon
    daily = client.query("daily", ts_code=ts_code, start_date="20230101",
                         end_date=end, fields="ts_code,trade_date,close")
    adj = client.query("adj_factor", ts_code=ts_code, start_date="20230101",
                       end_date=end, fields="ts_code,trade_date,adj_factor")
    factors = {str(r.get("trade_date")): float(r.get("adj_factor") or 1.0)
               for r in adj if r.get("trade_date")}
    closes: dict[str, float] = {}
    for row in daily:
        date = str(row.get("trade_date") or "")
        close = row.get("close")
        if date and close is not None:
            closes[date] = float(close) * factors.get(date, 1.0)
    base = _last_close_before(closes, formation)
    end_price = _last_close_before(closes, end)
    return {"base": base, "end": end_price,
            "return": (end_price / base - 1.0) if base and end_price else None}


def _st_after(client: TushareClient, ts_code: str, formation: str,
              end: str) -> bool:
    try:
        rows = client.query("namechange", ts_code=ts_code,
                            fields="ts_code,name,start_date,end_date,change_reason")
    except SqaDataError:
        return False
    for row in rows:
        name = str(row.get("name") or "")
        start = str(row.get("start_date") or "")
        stop = str(row.get("end_date") or "99999999")
        if "ST" not in name and "退" not in name:
            continue
        # 只统计形成日之后新发生的 ST/退市名称，形成日前已存在的不算未来标签
        if formation < start <= end and stop >= formation:
            return True
    return False


def _markdown(payload: dict) -> str:
    rows = payload["stocks"]
    valid = [r for r in rows if r.get("return_1y") is not None]
    lines = ["# F 步：历史风险快照 × 未来收益标签", "",
             f"- 形成日：{payload['formation']}；样本：{len(rows)} 只（收益有效 {len(valid)}）",
             f"- 生成时间：{payload['generated']}", "",
             "## 1. 风险 bucket 与未来收益", "",
             "| bucket | n | 1y mean | 1y median | 2y mean | 2y median |",
             "|---|---:|---:|---:|---:|---:|"]
    for bucket in ("pass", "degraded", "blocked"):
        group = [r for r in valid if r["verdict"] == bucket]
        if not group:
            continue
        one = [r["return_1y"] for r in group if r.get("return_1y") is not None]
        two = [r["return_2y"] for r in group if r.get("return_2y") is not None]
        lines.append(f"| {bucket} | {len(group)} | "
                     f"{np.mean(one):.2%} | {np.median(one):.2%} | "
                     f"{np.mean(two):.2%} | {np.median(two):.2%} |"
                     if one else f"| {bucket} | {len(group)} | - | - | - | - |")
    lines += ["", "## 2. 分数分位与未来收益", "",
              "| 风险分位 | n | 1y mean | 1y median | 2y mean |",
              "|---|---:|---:|---:|---:|"]
    for i, (label, group) in enumerate(payload["score_quartiles"].items()):
        one = [r["return_1y"] for r in group if r.get("return_1y") is not None]
        two = [r["return_2y"] for r in group if r.get("return_2y") is not None]
        if one:
            lines.append(f"| {label} | {len(group)} | {np.mean(one):.2%} | "
                         f"{np.median(one):.2%} | {np.mean(two):.2%} |")
    lines += ["", "## 3. 相关性", "",
              f"- score vs 1y return Spearman：{payload['corr']['score_1y']}",
              f"- score vs 2y return Spearman：{payload['corr']['score_2y']}",
              "", "## 4. 规则触发与 1y 收益（按 FAIL vs 非 FAIL 均值差排序）", "",
              "| 规则 | FAIL n | FAIL 1y mean | 非FAIL 1y mean | 差 |",
              "|---|---:|---:|---:|---:|"]
    for item in payload["rule_effects"][:12]:
        lines.append(f"| {item['rule']} | {item['n_fail']} | "
                     f"{item['fail_mean']:.2%} | {item['nonfail_mean']:.2%} | "
                     f"{item['diff']:.2%} |")
    lines += ["", "## 5. ST 标记", "",
              f"- 形成日后出现 ST/退市名称标记：{payload['st_count']} 只",
              "", "## 6. 限制", "",
              "- 样本为**当前上市**的 100 只，存在幸存者偏差；",
              "- 历史快照跳过质押/股东/同行/深度 PDF，风险分不完整；",
              "- 未处理停牌、交易成本、退市；",
              "- 收益为复权价（daily×adj_factor）近似，未扣分红税/费用。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="F 步历史风险×未来收益")
    parser.add_argument("--universe", default=str(PROJECT_ROOT / "config" / "backtest_universe_100.txt"))
    parser.add_argument("--formation", default="20230430")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cfg = load_thresholds()
    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))
    codes = [line.strip() for line in Path(args.universe).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if args.limit:
        codes = codes[:args.limit]
    formation = args.formation
    horizon_1 = str(int(formation[:4]) + 1) + formation[4:]
    horizon_2 = str(int(formation[:4]) + 2) + formation[4:]

    stocks = []
    for code in codes:
        ts_code = resolve_ts_code(code)
        try:
            risk = run_minesweeper(code, cfg=cfg, client=client, mode="quick",
                                   as_of=formation)
            ret1 = _future_return(client, ts_code, formation, horizon_1)
            ret2 = _future_return(client, ts_code, formation, horizon_2)
            st = _st_after(client, ts_code, formation, horizon_2)
        except Exception as exc:  # noqa: BLE001 - 单票失败不中断
            stocks.append({"symbol": code, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{code} ERROR {type(exc).__name__}: {exc}")
            continue
        stocks.append({
            "symbol": code, "name": risk["name"],
            "verdict": risk["verdict"], "risk_level": risk["risk_level"],
            "total_score": risk["total_score"],
            "return_1y": ret1["return"], "return_2y": ret2["return"],
            "base_close": ret1["base"], "st_after": st,
            "flagged": [f"{r['id']}:{r['verdict']}" for r in risk["rules"]
                        if r["verdict"] in ("WARN", "FAIL")],
            "rule_verdicts": {r["id"]: r["verdict"] for r in risk["rules"]},
        })
        print(f"{code} {risk['name']} {risk['verdict']} {risk['total_score']} "
              f"1y={ret1['return'] if ret1['return'] is None else round(ret1['return'], 3)} "
              f"2y={ret2['return'] if ret2['return'] is None else round(ret2['return'], 3)}")

    valid = [s for s in stocks if s.get("return_1y") is not None]
    score_quartiles: dict[str, list[dict]] = {}
    if valid:
        scores = [s["total_score"] for s in valid]
        quantiles = np.quantile(scores, [0.25, 0.5, 0.75])
        buckets = {"Q1(低风险分)": [], "Q2": [], "Q3": [], "Q4(高风险分)": []}
        for s in valid:
            idx = 0 if s["total_score"] <= quantiles[0] else \
                1 if s["total_score"] <= quantiles[1] else \
                2 if s["total_score"] <= quantiles[2] else 3
            list(buckets.values())[idx].append(s)
        score_quartiles = buckets

    def spearman(x, y):
        if len(x) < 5:
            return None
        rx = pd.Series(x).rank().to_numpy()
        ry = pd.Series(y).rank().to_numpy()
        return float(np.corrcoef(rx, ry)[0, 1])

    valid_1y = [s for s in valid if s.get("return_1y") is not None]
    valid_2y = [s for s in valid if s.get("return_2y") is not None]
    corr = {
        "score_1y": spearman([s["total_score"] for s in valid_1y],
                             [s["return_1y"] for s in valid_1y]),
        "score_2y": spearman([s["total_score"] for s in valid_2y],
                             [s["return_2y"] for s in valid_2y]),
    }

    rule_effects = []
    rule_ids = sorted({rid for s in valid_1y for rid in s["rule_verdicts"]})
    for rid in rule_ids:
        fail = [s["return_1y"] for s in valid_1y
                if s["rule_verdicts"].get(rid) == "FAIL"]
        non = [s["return_1y"] for s in valid_1y
               if s["rule_verdicts"].get(rid) != "FAIL"]
        if len(fail) >= 3 and non:
            rule_effects.append({"rule": rid, "n_fail": len(fail),
                                 "fail_mean": float(np.mean(fail)),
                                 "nonfail_mean": float(np.mean(non)),
                                 "diff": float(np.mean(fail) - np.mean(non))})
    rule_effects.sort(key=lambda x: x["diff"])

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "formation": formation, "horizon_1": horizon_1, "horizon_2": horizon_2,
        "universe": str(args.universe), "stocks": stocks,
        "score_quartiles": score_quartiles, "corr": corr,
        "rule_effects": rule_effects,
        "st_count": sum(1 for s in stocks if s.get("st_after")),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"f_labels_backtest_{stamp}.json"
    md_path = out_dir / f"f_labels_backtest_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(f"\nwritten {json_path}\nwritten {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
