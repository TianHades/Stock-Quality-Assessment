"""深度模式编排（02 号文档 §5）：获取 PDF → 转文本 → 抽取证据 → 规则判定。

返回结构：
{
  "status": "ok" | "skipped" | "error",   # skipped=无 PDF，error=有 PDF 但解析失败
  "message": "...",
  "pdf": {path, source, title, url, pages, backend},
  "rules": [MsResult, ...],               # 1.3 / 5.3 / 5.4 / 5.5 / 5.6 / 5.9 / 6.2
  "override_5_8": MsResult | None,        # 5.8 的 PDF 补充检查（命中才覆盖快筛）
  "evidence": {rule_id: {status, reason, hits: [{page, keyword, snippet}]}}
}
"""
from __future__ import annotations

from sqa.minesweeper.deep import extract as E
from sqa.minesweeper.deep import rules as R
from sqa.minesweeper.deep.pdf_download import resolve_pdf
from sqa.minesweeper.deep.pdf_text import PdfTextError, extract_pdf

_MAX_HITS = 3  # 报告里每条规则保留的证据片段数


def _trim_hits(payload: dict) -> list[dict]:
    # 证据优先级：FAIL/WARN 的实际命中优先于原始 hits（避免展示被否定的片段）
    hits = payload.get("fails") or payload.get("warns") or payload.get("hits") or []
    return [{k: hit.get(k) for k in ("page", "keyword", "snippet")}
            for hit in hits[:_MAX_HITS]]


def _failed(message: str, pdf_info: dict) -> dict:
    return {"status": "error", "message": message, "pdf": pdf_info,
            "rules": [], "override_5_8": None, "evidence": {}}


def _evidence(extracted: dict[str, dict]) -> dict[str, dict]:
    return {
        rule_id: {
            "status": payload.get("status", ""),
            "reason": payload.get("reason", ""),
            "hits": _trim_hits(payload),
        }
        for rule_id, payload in extracted.items()
    }


def run_deep(symbol: str, company_name: str, year: int,
             revenue_growth: float | None,
             pdf_path: str | None = None, pdf_url: str | None = None,
             refresh: bool = False) -> dict:
    """执行深度模式；任何失败都以状态返回，不抛异常（排雷主流程不中断）。"""
    pdf_info = {"path": "", "source": "", "title": "", "url": "",
                "pages": 0, "backend": ""}
    try:
        acquisition = resolve_pdf(symbol=symbol, company_name=company_name, year=year,
                                  pdf_path=pdf_path, pdf_url=pdf_url,
                                  refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - 文件系统/网络异常统一降级
        return _failed(f"PDF 获取失败: {type(exc).__name__}: {exc}", pdf_info)
    pdf_info.update({
        "path": acquisition.get("path", ""),
        "source": acquisition.get("source", ""),
        "title": acquisition.get("title", ""),
        "url": acquisition.get("url", ""),
    })
    if acquisition.get("status") != "ok":
        return {"status": acquisition.get("status", "error"),
                "message": acquisition.get("message", ""),
                "pdf": pdf_info, "rules": [], "override_5_8": None, "evidence": {}}

    try:
        document = extract_pdf(acquisition["path"], refresh=refresh)
    except PdfTextError as exc:
        return _failed(str(exc), pdf_info)
    except Exception as exc:  # noqa: BLE001 - 损坏/加密 PDF 的意外异常同样降级
        return _failed(f"PDF 解析异常: {type(exc).__name__}: {exc}", pdf_info)

    pdf_info.update({"pages": document.page_count, "backend": document.backend})

    try:
        freight = E.extract_freight(document.pages)
        customers = E.extract_customer_concentration(document.pages)
        cfo = E.extract_cfo_changes(document.pages)
        independent = E.extract_independent_director(document.pages)
        acquisitions = E.extract_cross_industry(document.pages)
        regulatory = E.extract_regulatory(document.pages)
        rd = E.extract_rd_capitalization(document.pages)
        occupation = E.extract_non_operating_occupation(document.pages)
        holder_events = E.extract_holder_events(document.pages)

        rules = [
            R.rule_1_3(freight, revenue_growth),
            R.rule_5_3(cfo),
            R.rule_5_4(independent),
            R.rule_5_5(customers),
            R.rule_5_6(acquisitions),
            R.rule_5_9(regulatory),
            R.rule_6_2(rd),
        ]
        evidence = _evidence({
            "1.3": freight, "5.3": cfo, "5.4": independent, "5.5": customers,
            "5.6": acquisitions, "5.2": holder_events, "5.8": occupation,
            "5.9": regulatory, "6.2": rd,
        })
    except Exception as exc:  # noqa: BLE001 - 证据抽取失败不中断排雷主流程
        return _failed(f"PDF 证据抽取失败: {type(exc).__name__}: {exc}", pdf_info)

    return {
        "status": "ok",
        "message": f"年报 PDF {document.page_count} 页（{document.backend}）",
        "pdf": pdf_info,
        "rules": rules,
        "override_5_8": R.supplement_5_8(occupation),
        "override_5_2": R.supplement_5_2(holder_events),
        "evidence": evidence,
    }
