#!/usr/bin/env python
"""G 步：多形成日 + 含退市/ST 股票池的历史风险标签回测。

相对 F 步的改进：
1. 股票池同时包含当前上市与后来退市（`build_historical_universe.py` 生成）；
2. 多个形成日（默认 2021/2022/2023-04-30），降低单一时点偶然性；
3. 历史风险快照补齐历史质押/股东/同行数据（as_of 版本），减少规则 SKIP；
4. 标签：1y/2y 复权收益 + ST 名称变化 + 退市日期；
   处罚标签因 tushare 无可用接口，暂不纳入（文档说明）。

仍是方向性验证：不处理停牌、交易成本、退市清算价；金融/医药已剔除。
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


def _asof_date(formation: str, years: int) -> str:
    return str(int(formation[:4]) + years) + formation[4:]


def _last_close_before(closes: dict[str, float], target: str) -> float | None:
    dates = [d for d in closes if d <= target]
    return closes[max(dates)] if dates else None


def _future_return(client: TushareClient, ts_code: str, formation: str,
                   horizon: str) -> dict:
    start = str(int(formation[:4]) - 1) + "0101"
    try:
        daily = client.query("daily", ts_code=ts_code, start_date=start,
                             end_date=horizon, fields="ts_code,trade_date,close")
        adj = client.query("adj_factor", ts_code=ts_code, start_date=start,
                           end_date=horizon, fields="ts_code,trade_date,adj_factor")
    except SqaDataError:
        return {"base": None, "end": None, "return": None}
    factors = {str(r.get("trade_date")): float(r.get("adj_factor") or 1.0)
               for r in adj if r.get("trade_date")}
    closes: dict[str, float] = {}
    for row in daily:
        date = str(row.get("trade_date") or "")
        close = row.get("close")
        if date and close is not None:
            closes[date] = float(close) * factors.get(date, 1.0)
    base = _last_close_before(closes, formation)
    end = _last_close_before(closes, horizon)
    return {"base": base, "end": end,
            "return": (end / base - 1.0) if base and end else None}


def _name_events(client: TushareClient, ts_code: str, formation: str,
                 end: str) -> dict:
    try:
        rows = client.query("namechange", ts_code=ts_code,
                            fields="ts_code,name,start_date,end_date,change_reason")
    except SqaDataError:
        return {"st": False, "delist_name": False}
    st = delist_name = False
    for row in rows:
        name = str(row.get("name") or "")
        start = str(row.get("start_date") or "")
        stop = str(row.get("end_date") or "99999999")
        # 只统计形成日之后“新发生”的 ST；形成日前已 ST 不属于未来标签
        if formation < start <= end and stop >= formation:
            if "ST" in name:
                st = True
            if "退" in name:
                delist_name = True
    return {"st": st, "delist_name": delist_name}


def _audit_event(client: TushareClient, ts_code: str, formation: str,
                 end: str) -> bool:
    """形成日后是否出现非标准审计意见（可用的“处罚/风险事件”代理标签）。"""
    try:
        rows = client.query("fina_audit", ts_code=ts_code,
                            fields="ts_code,end_date,ann_date,audit_result")
    except SqaDataError:
        return False
    for row in rows:
        result = str(row.get("audit_result") or "").strip()
        ann_date = str(row.get("ann_date") or row.get("end_date") or "")
        if result and "标准无保留" not in result and formation < ann_date <= end:
            return True
    return False


def _markdown(payload: dict) -> str:
    lines = ["# G 步：多形成日 × 退市/ST 标签回测", "",
             f"- 形成日：{', '.join(payload['formations'])}；历史股票池：{payload['universe']}",
             f"- 有效观测：{len(payload['observations'])}（股票数 {len(payload['codes'])}）",
             f"- 生成时间：{payload['generated']}", "",
             "## 1. 分形成日风险 bucket 与 1y/2y 收益", "",
             "| 形成日 | bucket | n | 1y mean | 1y median | 2y mean |",
             "|---|---|---:|---:|---:|---:|"]
    for f, groups in payload["by_formation"].items():
        for bucket in ("excluded", "blocked", "degraded", "pass"):
            g = groups.get(bucket) or []
            if not g:
                continue
            one = [x["return_1y"] for x in g if x.get("return_1y") is not None]
            two = [x["return_2y"] for x in g if x.get("return_2y") is not None]
            if one:
                lines.append(f"| {f} | {bucket} | {len(g)} | {np.mean(one):.2%} | "
                             f"{np.median(one):.2%} | {np.mean(two):.2%} |")
    lines += ["", "## 2. 总体 bucket", "",
              "| bucket | n | 1y mean | 1y median | 2y mean | 2y median | ST 率 | 退市率 | 非标审计率 | 任一事件率 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for bucket in ("excluded", "blocked", "degraded", "pass"):
        g = [x for x in payload["observations"] if x["verdict"] == bucket]
        if not g:
            continue
        one = [x["return_1y"] for x in g if x.get("return_1y") is not None]
        two = [x["return_2y"] for x in g if x.get("return_2y") is not None]
        st_rate = sum(1 for x in g if x.get("st_after")) / len(g)
        del_rate = sum(1 for x in g if x.get("delisted_after")) / len(g)
        audit_rate = sum(1 for x in g if x.get("audit_nonstandard")) / len(g)
        any_rate = sum(1 for x in g if x.get("event_any")) / len(g)
        if one:
            lines.append(f"| {bucket} | {len(g)} | {np.mean(one):.2%} | {np.median(one):.2%} | "
                         f"{np.mean(two):.2%} | {np.median(two):.2%} | {st_rate:.0%} | "
                         f"{del_rate:.0%} | {audit_rate:.0%} | {any_rate:.0%} |")
    lines += ["", "## 3. 相关性", "",
              f"- score vs 1y：{payload['corr']['score_1y']}",
              f"- score vs 2y：{payload['corr']['score_2y']}",
              "", "## 4. 事件标签", "",
              f"- 观测数：{len(payload['observations'])}；"
              f"ST：{payload['event_counts']['st']}；退市：{payload['event_counts']['delist']}；"
              f"非标审计：{payload['event_counts']['audit']}；"
              f"任一事件：{payload['event_counts']['any']}",
              "", "## 5. 规则 FAIL 与 1y 收益（差=FAIL 组减非 FAIL）", "",
              "| 规则 | FAIL n | FAIL 1y | 非FAIL 1y | 差 |",
              "|---|---:|---:|---:|---:|"]
    for item in payload["rule_effects"][:15]:
        lines.append(f"| {item['rule']} | {item['n_fail']} | {item['fail_mean']:.2%} | "
                     f"{item['nonfail_mean']:.2%} | {item['diff']:+.2%} |")
    lines += ["", "## 6. 限制", "",
              f"- 股票池 {len(payload['codes'])} 只"
              f"（{payload.get('delisted_count', '?')} 只后来退市/ST），仍不是全市场；",
              "- 历史风险快照已补质押/股东/同行，但同行池仍为当前上市 L、"
              "当前行业分类（轻微幸存者/行业偏差），且仍跳过深度 PDF；",
              "- 退市收益取退市前最后价格，不是清算价值；未处理停牌/交易成本；",
              "- 处罚标签缺 tushare 接口，未纳入；非标审计仅作为风险事件代理；",
              "- 多个形成日仍可能受样本/宏观周期影响。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="G 步多形成日+退市标签回测")
    parser.add_argument("--universe", default=str(PROJECT_ROOT / "config" / "backtest_universe_hist.txt"))
    parser.add_argument("--formations", default="20210430,20220430,20230430")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--from-json", default="",
                        help="只从已有 JSON 重新渲染 Markdown（不重新取数）")
    args = parser.parse_args()

    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        md_path = out_dir / f"g_labels_backtest_{stamp}.md"
        md_path.write_text(_markdown(payload), encoding="utf-8")
        print(f"written {md_path}")
        return 0

    cfg = load_thresholds()
    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))
    codes = [line.strip() for line in Path(args.universe).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if args.limit:
        codes = codes[:args.limit]
    formations = [f.strip() for f in args.formations.split(",") if f.strip()]

    # 退市日期/上市日期元数据
    info: dict[str, dict] = {}
    for status in ("L", "D"):
        rows = client.query("stock_basic", list_status=status,
                            fields="ts_code,symbol,name,industry,list_date,delist_date")
        for row in rows:
            code = str(row.get("symbol") or str(row.get("ts_code") or "").split(".")[0])
            info[code] = row

    observations: list[dict] = []
    for formation in formations:
        h1 = _asof_date(formation, 1)
        h2 = _asof_date(formation, 2)
        for code in codes:
            meta = info.get(code)
            if not meta:
                continue
            list_date = str(meta.get("list_date") or "")
            delist_date = str(meta.get("delist_date") or "")
            if not list_date or list_date > formation:
                continue
            if delist_date and delist_date <= formation:
                continue
            ts_code = resolve_ts_code(code)
            try:
                risk = run_minesweeper(code, cfg=cfg, client=client, mode="quick",
                                       as_of=formation)
                r1 = _future_return(client, ts_code, formation, h1)
                r2 = _future_return(client, ts_code, formation, h2)
                events = _name_events(client, ts_code, formation, h2)
                audit_nonstandard = _audit_event(client, ts_code, formation, h2)
            except Exception as exc:  # noqa: BLE001
                print(f"{formation} {code} ERROR {type(exc).__name__}: {exc}")
                continue
            delisted_after = bool(delist_date and formation < delist_date <= h2)
            observations.append({
                "formation": formation, "symbol": code, "name": risk["name"],
                "verdict": risk["verdict"], "risk_level": risk["risk_level"],
                "total_score": risk["total_score"],
                "return_1y": r1["return"], "return_2y": r2["return"],
                "st_after": events["st"], "delisted_after": delisted_after,
                "audit_nonstandard": audit_nonstandard,
                "event_any": bool(events["st"] or delisted_after or audit_nonstandard),
                "rule_verdicts": {r["id"]: r["verdict"] for r in risk["rules"]},
            })
            print(f"{formation} {code} {risk['name']} {risk['verdict']} "
                  f"{risk['total_score']} 1y={r1['return']} delist={delisted_after}")

    # 分形成日聚合
    by_formation: dict[str, dict[str, list[dict]]] = {}
    for obs in observations:
        by_formation.setdefault(obs["formation"], {}).setdefault(obs["verdict"], []).append(obs)

    valid = [o for o in observations if o.get("return_1y") is not None]

    def spearman(x, y):
        if len(x) < 5:
            return None
        return float(np.corrcoef(pd.Series(x).rank(), pd.Series(y).rank())[0, 1])

    corr = {
        "score_1y": spearman([o["total_score"] for o in valid],
                             [o["return_1y"] for o in valid]),
        "score_2y": spearman([o["total_score"] for o in observations
                              if o.get("return_2y") is not None],
                             [o["return_2y"] for o in observations
                              if o.get("return_2y") is not None]),
    }
    rule_ids = sorted({rid for o in valid for rid in o["rule_verdicts"]})
    rule_effects = []
    for rid in rule_ids:
        fail = [o["return_1y"] for o in valid if o["rule_verdicts"].get(rid) == "FAIL"]
        non = [o["return_1y"] for o in valid if o["rule_verdicts"].get(rid) != "FAIL"]
        if len(fail) >= 3 and non:
            rule_effects.append({"rule": rid, "n_fail": len(fail),
                                 "fail_mean": float(np.mean(fail)),
                                 "nonfail_mean": float(np.mean(non)),
                                 "diff": float(np.mean(fail) - np.mean(non))})
    rule_effects.sort(key=lambda x: x["diff"])

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "formations": formations, "universe": str(args.universe), "codes": codes,
        "delisted_count": sum(1 for c in codes
                              if str(info.get(c, {}).get("delist_date") or "")),
        "observations": observations, "by_formation": by_formation,
        "corr": corr, "rule_effects": rule_effects,
        "event_counts": {
            "st": sum(1 for o in observations if o["st_after"]),
            "delist": sum(1 for o in observations if o["delisted_after"]),
            "audit": sum(1 for o in observations if o["audit_nonstandard"]),
            "any": sum(1 for o in observations if o["event_any"]),
        },
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"g_labels_backtest_{stamp}.json"
    md_path = out_dir / f"g_labels_backtest_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(f"\nwritten {json_path}\nwritten {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
