#!/usr/bin/env python
"""生成扩样回测股票池（E：100 只分层样本）。

选样规则（可复现）：
1. A 股 stock_basic（list_status=L），仅 0/3/6 开头；
2. 剔除 ST/退市、医药生物、金融（复用 classify.exclusions）；
3. 上市日期早于 2018-01-01（保证至少 8 年数据）；
4. 按 tushare industry 分组，每组按总市值取前 3 家；不足 100 时用剩余
   大市值股票补足，超过 100 时取市值最大的 100 只；
5. 输出 config/backtest_universe_100.txt。

用法：
    python scripts/build_backtest_universe.py
    python scripts/build_backtest_universe.py --size 100 --out config/backtest_universe_100.txt
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path

from sqa.classify.exclusions import exclusion_reason
from sqa.config import PROJECT_ROOT, load_thresholds
from sqa.data.tushare_client import TushareClient
from sqa.quality.fetch import _latest_market_mv


def main() -> int:
    parser = argparse.ArgumentParser(description="生成分层回测股票池")
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--per-industry", type=int, default=3)
    parser.add_argument("--min-list-date", default="20180101")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "config" / "backtest_universe_100.txt"))
    args = parser.parse_args()

    load_thresholds()
    client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))
    basics = client.query("stock_basic", list_status="L",
                          fields="ts_code,symbol,name,industry,list_date")
    mv = _latest_market_mv(client, False)

    candidates = []
    for row in basics:
        code = str(row.get("symbol") or row.get("ts_code") or "")
        industry = str(row.get("industry") or "")
        name = str(row.get("name") or "")
        list_date = str(row.get("list_date") or "")
        if not code.startswith(("0", "3", "6")) or not industry or not name:
            continue
        if list_date and list_date > args.min_list_date:
            continue
        if exclusion_reason(name, industry):
            continue
        candidates.append({"symbol": code, "name": name, "industry": industry,
                           "mv": mv.get(str(row.get("ts_code")), 0.0)})

    by_industry: dict[str, list[dict]] = defaultdict(list)
    for item in candidates:
        by_industry[item["industry"]].append(item)
    for items in by_industry.values():
        items.sort(key=lambda x: x["mv"], reverse=True)

    selected: dict[str, dict] = {}
    for industry in sorted(by_industry):
        for item in by_industry[industry][:args.per_industry]:
            selected[item["symbol"]] = item

    if len(selected) < args.size:
        rest = sorted((item for item in candidates if item["symbol"] not in selected),
                      key=lambda x: x["mv"], reverse=True)
        for item in rest:
            selected[item["symbol"]] = item
            if len(selected) >= args.size:
                break
    if len(selected) > args.size:
        selected = dict(sorted(selected.items(), key=lambda kv: kv[1]["mv"], reverse=True)[:args.size])

    lines = [
        "# E 步扩样回测股票池（脚本生成，可重跑）",
        f"# 选样：A股、上市<={args.min_list_date}、剔除ST/医药/金融、"
        f"按行业市值前{args.per_industry}、目标{args.size}只",
        f"# 实际 {len(selected)} 只 / {len({v['industry'] for v in selected.values()})} 个行业",
    ]
    for item in sorted(selected.values(), key=lambda x: x["symbol"]):
        lines.append(item["symbol"])
    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(selected)} stocks, "
          f"{len({v['industry'] for v in selected.values()})} industries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
