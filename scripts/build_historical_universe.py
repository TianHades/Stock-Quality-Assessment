#!/usr/bin/env python
"""生成 G 步历史回测股票池（含后来退市/ST 公司）。

目的：修正 F 步“只取当前上市 100 只”的幸存者偏差。
做法：
1. stock_basic 同时查询 L（当前上市）与 D（已退市）；
2. 要求股票在形成日（默认 2021-04-30）已上市、且尚未退市；
3. 剔除金融/医药（按行业；保留 ST/退市名称，因为它们正是风险结果标签）；
4. 历史样本 = 20 只后来退市 + 40 只当前上市（按行业分层、固定哈希排序，
   不使用当前市值，避免选择偏差）；
5. 输出 config/backtest_universe_hist.txt。

用途：`scripts/g_labels_backtest.py` 多形成日回测。
"""
from __future__ import annotations

import argparse
import hashlib
import os
from collections import defaultdict
from pathlib import Path

from sqa.classify.exclusions import exclusion_reason
from sqa.config import PROJECT_ROOT, load_thresholds
from sqa.data.tushare_client import TushareClient


def _stable_key(code: str, seed: str) -> str:
    return hashlib.sha1(f"{seed}:{code}".encode("utf-8")).hexdigest()


_EXCLUDED_INDUSTRY_KEYWORDS = (
    "医药", "制药", "医疗", "生物", "中药", "中成药", "器械", "疫苗",
    "银行", "证券", "保险", "信托", "期货", "多元金融",
)


def _industry_excluded(name: str, industry: str) -> bool:
    # 先按行业关键词判断，避免名称带 ST 时 exclusion_reason 先返回名称原因
    if any(k in (industry or "") for k in _EXCLUDED_INDUSTRY_KEYWORDS):
        return True
    reason = exclusion_reason(name, industry) or ""
    return "医药" in reason or "金融" in reason


def main() -> int:
    parser = argparse.ArgumentParser(description="生成含退市股的历史回测股票池")
    parser.add_argument("--formation", default="20210430")
    parser.add_argument("--size", type=int, default=60)
    parser.add_argument("--delisted", type=int, default=20)
    parser.add_argument("--min-list-date", default="20160101")
    parser.add_argument("--seed", default="g-backtest-2026")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "config" / "backtest_universe_hist.txt"))
    args = parser.parse_args()

    load_thresholds()
    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))
    rows = []
    for status in ("L", "D"):
        rows.extend(client.query(
            "stock_basic", list_status=status,
            fields="ts_code,symbol,name,industry,list_date,delist_date"))

    active = []
    seen = set()
    for row in rows:
        code = str(row.get("symbol") or str(row.get("ts_code") or "").split(".")[0])
        if len(code) != 6 or not code.startswith(("0", "3", "6")) or code in seen:
            continue
        list_date = str(row.get("list_date") or "")
        delist_date = str(row.get("delist_date") or "")
        if not list_date or list_date > args.min_list_date:
            continue
        if list_date > args.formation:
            continue
        if delist_date and delist_date <= args.formation:
            continue
        industry = str(row.get("industry") or "")
        if _industry_excluded(str(row.get("name") or ""), industry):
            continue
        seen.add(code)
        active.append({"code": code, "name": str(row.get("name") or ""),
                       "industry": industry or "其他",
                       "delist_date": delist_date})

    delisted = [r for r in active if r["delist_date"]]
    listed = [r for r in active if not r["delist_date"]]
    delisted.sort(key=lambda r: _stable_key(r["code"], args.seed))
    selected_delisted = delisted[:args.delisted]
    target_listed = max(0, args.size - len(selected_delisted))

    by_industry: dict[str, list[dict]] = defaultdict(list)
    for row in listed:
        by_industry[row["industry"]].append(row)
    selected_listed: list[dict] = []
    for industry in sorted(by_industry):
        if len(selected_listed) >= target_listed:
            break
        pool = sorted(by_industry[industry],
                      key=lambda r: _stable_key(r["code"], args.seed))
        if pool:
            selected_listed.append(pool[0])
    rest = [r for r in listed if r not in selected_listed]
    rest.sort(key=lambda r: _stable_key(r["code"], args.seed))
    for row in rest:
        if len(selected_listed) >= target_listed:
            break
        selected_listed.append(row)
    selected = (selected_delisted + selected_listed)[:args.size]
    selected.sort(key=lambda r: r["code"])

    lines = [
        "# G 步历史回测股票池（脚本生成，可重跑）",
        f"# 形成日 {args.formation}；当前上市 {len(selected_listed)} + 后来退市 {len(selected_delisted)}",
        "# 已剔除金融/医药；保留 ST/退市名称作为结果标签；按行业分层+哈希，不使用市值",
    ]
    lines += [row["code"] for row in selected]
    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(selected)} stocks "
          f"({len(selected_delisted)} delisted, {len(selected_listed)} listed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
