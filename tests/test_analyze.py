"""analyze 编排的异常降级测试（离线，monkeypatch）。"""
from __future__ import annotations

import sqa.analyze as analyze
from sqa.data.tushare_client import SqaDataError


def test_analyze_stops_cleanly_on_data_error(monkeypatch):
    def boom(*args, **kwargs):
        raise SqaDataError("模拟 tushare 网络失败")

    monkeypatch.setattr(analyze, "classify_stock", boom)
    monkeypatch.setattr(analyze, "_save", lambda result: None)
    result = analyze.run_analyze("600519")
    assert result["pipeline"] == "stopped:data_error"
    assert "分类取数失败" in result["stop_reason"]
