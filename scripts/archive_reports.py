#!/usr/bin/env python
"""归档旧分析报告（A 步工程收口用，可重复执行）。

用法：
    python scripts/archive_reports.py                 # 归档 2026-10-19 之前
    python scripts/archive_reports.py --cutoff 20261019
    python scripts/archive_reports.py --dry-run

只移动 reports/analyze_*.json，不碰验收报告/说明文件；目标目录默认
reports/archive_pre_<cutoff>/，已存在同名文件时加序号避免覆盖。
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_RE = re.compile(r"analyze_[^_]+_(\d{8})_")


def main() -> int:
    parser = argparse.ArgumentParser(description="归档旧分析报告")
    parser.add_argument("--cutoff", default=datetime.now().strftime("%Y%m%d"),
                        help="归档早于该日期的报告（YYYYMMDD，默认今天）")
    parser.add_argument("--reports-dir", default=str(ROOT / "reports"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    archive_dir = reports_dir / f"archive_pre_{args.cutoff}"
    moved = 0
    for path in sorted(reports_dir.glob("analyze_*.json")):
        match = REPORT_RE.search(path.name)
        if not match or int(match.group(1)) >= int(args.cutoff):
            continue
        target = archive_dir / path.name
        if target.exists():
            target = archive_dir / f"{path.stem}_{moved}{path.suffix}"
        if args.dry_run:
            print(f"[dry-run] {path.name} -> {target}")
            moved += 1
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        moved += 1
    print(f"归档 {moved} 份旧报告 -> {archive_dir}" if not args.dry_run
          else f"将归档 {moved} 份旧报告 -> {archive_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
