"""总编排（pipeline 串联）：分类判定 → 排雷闸门 → 质量评分 → 模块 → 估值 → 报告落盘。

对应架构：指标计算 → 排雷(一票否决，可选深度模式) → 类别判定 → 加权评分 → 专属分析(04) → 估值(05)
"""
from __future__ import annotations

import json
import time

from sqa.classify.pipeline import classify_stock
from sqa.config import PROJECT_ROOT
from sqa.data.tushare_client import SqaDataError
from sqa.minesweeper.engine import run_minesweeper
from sqa.modules.runner import run_modules
from sqa.quality.engine import run_quality
from sqa.valuation.engine import run_valuation


def _stopped(result: dict, reason: str) -> dict:
    result["pipeline"] = "stopped:data_error"
    result["stop_reason"] = reason
    _save(result)
    return result


def run_analyze(symbol: str, refresh: bool = False, force: bool = False,
                deep: bool = False, pdf_path: str | None = None,
                pdf_url: str | None = None) -> dict:
    """一条命令跑完整质地分析（分类 → 排雷 → 评分）。"""
    result: dict = {"symbol": symbol, "name": ""}

    try:
        cls = classify_stock(symbol, refresh=refresh)
    except SqaDataError as exc:
        return _stopped(result, f"分类取数失败: {exc}")
    result["classification"] = cls
    result["symbol"], result["name"] = cls["symbol"], cls["name"]

    if cls["style"] == "OUT_OF_SCOPE":
        result["pipeline"] = "stopped:out_of_scope"
        result["stop_reason"] = cls["warnings"]
        _save(result)
        return result

    try:
        ms = run_minesweeper(symbol, refresh=refresh,
                             mode="deep" if deep else "quick",
                             pdf_path=pdf_path, pdf_url=pdf_url)
    except SqaDataError as exc:
        return _stopped(result, f"排雷取数失败: {exc}")
    result["minesweeper"] = {
        k: ms[k] for k in ("verdict", "risk_level", "total_score",
                           "base_score", "combo_bonus", "combos", "layer_scores",
                           "forceable", "deep")
    }
    result["minesweeper"]["flagged"] = [
        {"id": r["id"], "layer": r["layer"], "verdict": r["verdict"], "detail": r["detail"]}
        for r in ms["rules"] if r["verdict"] in ("WARN", "FAIL")
    ]

    can_force = bool(force and ms.get("forceable"))
    if ms["verdict"] in ("excluded", "blocked") and not can_force:
        if ms["verdict"] == "excluded":
            reason = "L0 门槛失败（审计意见/披露时效），--force 不可绕过"
        elif not ms.get("forceable"):
            reason = "极高风险出局（总分 46+），--force 不可绕过"
        else:
            reason = "高风险出局；可 --force 强制继续（评级双降）"
        result["pipeline"] = "stopped:risk_gate"
        result["stop_reason"] = (
            f"排雷闸门拦截: {ms['verdict']}（{ms['risk_level']}，总分 {ms['total_score']}）。{reason}"
        )
        _save(result)
        return result

    try:
        quality = run_quality(
            symbol, style=cls["style"], badges=cls["badges"],
            ms_verdict=ms["verdict"], refresh=refresh,
        )
    except SqaDataError as exc:
        return _stopped(result, f"质量评分取数失败: {exc}")
    result["quality"] = quality
    try:
        modules = run_modules(cls, refresh=refresh)
    except SqaDataError as exc:
        return _stopped(result, f"专属模块取数失败: {exc}")
    result["modules"] = modules

    cycle_pos = (modules.get("cycle_position") or {}).get("position")
    try:
        result["valuation"] = run_valuation(
            symbol, style=cls["style"], badges=cls["badges"],
            cycle_position=cycle_pos, refresh=refresh,
        )
    except SqaDataError as exc:
        return _stopped(result, f"估值取数失败: {exc}")
    result["pipeline"] = "complete"
    _save(result)
    return result


def _save(result: dict) -> None:
    out_dir = PROJECT_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"analyze_{result['symbol']}_{time.strftime('%Y%m%d_%H%M')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
