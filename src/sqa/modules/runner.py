"""模块调度器：按分类输出的 modules 清单执行专属分析。"""
from __future__ import annotations

import os

from sqa.config import Thresholds, load_thresholds
from sqa.data.fetchers import resolve_ts_code
from sqa.data.tushare_client import TushareClient
from sqa.modules import cycle_position, dividend_sustainability, growth_quality, moat_check

_MODULE_FUNCS = {
    "cycle_position": cycle_position.run,
    "dividend_sustainability": dividend_sustainability.run,
    "growth_quality_check": growth_quality.run,
    "moat_check": moat_check.run,
}


def run_modules(cls_result: dict, cfg: Thresholds | None = None,
                client: TushareClient | None = None,
                refresh: bool = False) -> dict[str, dict]:
    """执行分类结果里触发的全部模块，返回 {module_name: result}。

    单个模块异常不中断其他模块（错误写入该模块条目）。
    """
    cfg = cfg or load_thresholds()
    if client is None:
        client = TushareClient(token=os.environ.get("TUSHARE_TOKEN", ""))

    ts_code = resolve_ts_code(cls_result.get("symbol", ""))
    style = cls_result.get("style", "")
    out: dict[str, dict] = {}
    for name in cls_result.get("modules", []):
        func = _MODULE_FUNCS.get(name)
        if func is None:
            out[name] = {"module": name, "error": "未注册的模块"}
            continue
        try:
            out[name] = func(ts_code, style, client, cfg, refresh)
        except Exception as exc:  # noqa: BLE001 - 模块失败不阻断报告
            out[name] = {"module": name, "error": f"{type(exc).__name__}: {exc}"}
    return out
