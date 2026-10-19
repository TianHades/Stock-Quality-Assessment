"""年报 PDF 文本证据抽取（深度模式）。

设计原则：只做"可验证的证据抽取"，不硬造判定——
- 数值型规则（运费/客户集中度/研发资本化）给出可复算的数值；
- 判断型规则（财务负责人/独董/收购/监管处罚）给出原文片段，判定交由 rules.py
  保守处理（证据不足时保持 SKIP，绝不猜测）。

2026-10-19 加固：保留换行以识别表格行边界；5.8/5.9 解析"√适用/√否"勾选项而非
全窗搜子串；5.5 支持汇总与明细跨页；6.2 绑定百分比标签/金额除法；5.3/5.4 按句
判定并扩大扫描窗口。
"""
from __future__ import annotations

import re

_MAX_SNIPPET = 220
_NUM = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_CHECK_MARK = r"[√☑☒✓✔■]"


def snippets(pages: list[str], keywords: tuple[str, ...],
             context: int = _MAX_SNIPPET, max_hits: int = 6) -> list[dict]:
    """在全文按页检索关键词，返回去重后的上下文片段（含页码）。

    仅压缩行内空白、保留 ``\\n``：表格行/段落边界是后续"同一行/同一句"判定
    的关键，不能全部压成空格。
    """
    hits: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for page_no, page in enumerate(pages, start=1):
        if not page:
            continue
        for keyword in keywords:
            start = 0
            while len(hits) < max_hits:
                pos = page.find(keyword, start)
                if pos < 0:
                    break
                start = pos + len(keyword)
                left, right = max(0, pos - context), min(len(page), pos + len(keyword) + context)
                key = (page_no, left // 40)
                if key in seen:
                    continue
                seen.add(key)
                snippet = re.sub(r"[ \t\u3000]+", " ", page[left:right]).strip()
                hits.append({"page": page_no, "keyword": keyword, "snippet": snippet})
    return hits


def _sentences(text: str) -> list[str]:
    """按句号/分号/换行切句，保留足够短的判定单元。"""
    return [s.strip() for s in re.split(r"[。；;\n]", text) if s.strip()]


def numbers(text: str, min_abs: float = 1.0) -> list[float]:
    """提取数值：排除百分比与 4 位年份；金额单位保持原文（比值不受影响）。"""
    out: list[float] = []
    for match in _NUM.finditer(text):
        raw = match.group(0).replace(",", "").replace("，", "").lstrip("+")
        try:
            value = float(raw)
        except ValueError:
            continue
        if text[match.end():match.end() + 3].lstrip().startswith("%"):  # 百分比
            continue
        if _YEAR.match(raw) and 1990 <= value <= 2035:
            continue
        if abs(value) < min_abs:
            continue
        out.append(value)
    return out


def percentages(text: str) -> list[float]:
    return [float(m.group(1)) for m in _PCT.finditer(text)]


# ---------- 1.3 运费增长 vs 收入增长 ----------

_FREIGHT_KW = ("运输费", "运费", "装卸费")


def _amount_pair(text: str, keyword: str) -> tuple[float, float] | None:
    """从同一行的"金额 占比 金额 占比"或"金额 金额"中提取（本期, 上期）。

    年报附注表格经 PDF 抽取后常见：
      运输费 326,866,386.45 2.21 311,524,380.58 2.29
    取关键词后 180 字符内数值：优先"大数-小数-大数"列式，再回退前两个金额；
    比例校验 0.05–20 倍，异常放弃。
    """
    pos = text.find(keyword)
    if pos < 0:
        return None
    tail = text[pos + len(keyword):pos + len(keyword) + 180]
    tokens: list[tuple[float, bool]] = []  # (值, 是否非金额标记)
    for match in _NUM.finditer(tail):
        raw = match.group(0).replace(",", "").replace("，", "").lstrip("+")
        try:
            value = float(raw)
        except ValueError:
            continue
        tail_after = tail[match.end():match.end() + 3]
        if tail_after.lstrip().startswith("%") or (
                _YEAR.match(raw) and 1990 <= value <= 2035):
            tokens.append((value, True))
        else:
            tokens.append((value, abs(value) < 100))

    for i in range(len(tokens) - 2):
        first, first_small = tokens[i]
        between, between_small = tokens[i + 1]
        third, third_small = tokens[i + 2]
        if first_small or not between_small or third_small:
            continue
        return _checked_pair(first, third)

    amounts = [value for value, small in tokens if not small and abs(value) >= 100]
    if len(amounts) >= 2:
        return _checked_pair(amounts[0], amounts[1])
    return None


def _checked_pair(current: float, previous: float) -> tuple[float, float] | None:
    if previous <= 0 or current <= 0:
        return None
    ratio = current / previous
    if not 0.05 <= ratio <= 20:  # 同表两列应同量级；异常配对放弃
        return None
    return current, previous


def extract_freight(pages: list[str]) -> dict:
    """从销售费用/营业成本附注中抽取本期与上期运输费金额（同一行判定）。"""
    hits = snippets(pages, _FREIGHT_KW, context=240, max_hits=12)
    if not hits:
        return {"status": "missing", "reason": "附注未单独披露运输费（新准则下常计入营业成本）",
                "hits": []}
    for hit in hits:
        lines = hit["snippet"].splitlines()
        for idx, line in enumerate(lines):
            if hit["keyword"] not in line:
                continue
            pair = _amount_pair(line, hit["keyword"])
            if pair is None and idx + 1 < len(lines):
                continuation = lines[idx + 1].strip()
                # 仅允许"纯数字续行"；下一行若出现中文项目名（如 折旧费）则拒绝，
                # 避免把相邻项目的金额误当运输费上期。
                if continuation and not re.search(r"[\u4e00-\u9fff]", continuation):
                    pair = _amount_pair(line + " " + continuation, hit["keyword"])
            if pair is not None:
                current, previous = pair
                return {"status": "ok", "current": current, "previous": previous,
                        "growth": current / previous - 1, "hits": hits}
    return {"status": "ambiguous", "reason": "检出运输费表述，但未能可靠提取同一行的本期/上期金额",
            "hits": hits}


# ---------- 5.5 客户集中度 ----------

_CUSTOMER_KW = ("前五名客户", "前五大客户", "前5名客户", "前 5 大客户",
                "前5大客户", "客户集中度")
_CUSTOMER_HEAD = r"(?:前五名客户|前五大客户|前5名客户|前\s*5\s*大客户)"


def _customer_total(text: str) -> float | None:
    """前五客户合计占比（限定客户/销售语境，避免误取供应商"采购总额"）。"""
    patterns = (
        _CUSTOMER_HEAD + r"[^。；;]{0,300}?(?:合计|累计|占|比例)[^。；;]{0,60}?"
        r"(\d+(?:\.\d+)?)\s*%",
        _CUSTOMER_HEAD + r"[^。；;]{0,300}?(?:销售总额|营业收入|销售总额占比)"
        r"[^。；;]{0,20}?(\d+(?:\.\d+)?)\s*%",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _customer_single(text: str) -> float | None:
    match = re.search(
        r"(?:第一名|第一大客户|客户一|客户1|单一客户)[^。；;]{0,60}?"
        r"(\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) if match else None


def extract_customer_concentration(pages: list[str]) -> dict:
    """客户集中度：汇总/明细可能跨页，扫描关键词页与下一页。"""
    hits = snippets(pages, _CUSTOMER_KW, context=240, max_hits=20)
    if not hits:
        return {"status": "missing", "hits": []}
    for hit in hits:
        text = hit["snippet"]
        page_index = hit["page"] - 1
        if page_index + 1 < len(pages):  # 汇总在页末、明细在下一页是常见版式
            text = text + "\n" + pages[page_index + 1][:2000]
        total = _customer_total(text)
        single = _customer_single(text)
        if total is None and single is None:
            continue
        return {"status": "ok", "total": total, "single": single, "hits": hits}
    return {"status": "ambiguous", "hits": hits}


# ---------- 5.3 / 5.4 财务负责人、独立董事变动 ----------

_LEAVE_KW = ("离任", "辞职", "辞任", "辞去", "解聘", "聘任", "任命",
             "不再担任", "工作变动", "换届")
_CFO_KW = ("财务总监", "财务负责人", "首席财务官", "CFO")


def _proximity_change(hits: list[dict], change_kw: tuple[str, ...]) -> list[dict]:
    """按句/表格行判定变动：优先同句，其次关键词两侧 45 字符窗口。

    PDF 表格常把"财务总监"和"聘任/离任"拆到相邻行；同句判定会漏，故保留
    一个窄窗口兜底（限制 45 字符以降低把相邻高管的变动算到本人头上的风险）。
    """
    changed: list[dict] = []
    for hit in hits:
        snippet = hit["snippet"]
        pos = snippet.find(hit["keyword"])
        if pos < 0:
            continue
        found = False
        for sentence in _sentences(snippet):
            if hit["keyword"] in sentence and any(word in sentence for word in change_kw):
                changed.append({**hit, "snippet": sentence})
                found = True
        if found:
            continue
        left = max(0, pos - 40)
        right = min(len(snippet), pos + len(hit["keyword"]) + 50)
        window = re.sub(r"[ \t\u3000]+", " ", snippet[left:right]).strip()
        if any(word in window for word in change_kw):
            changed.append({**hit, "snippet": window})
    return changed


def extract_cfo_changes(pages: list[str]) -> dict:
    hits = snippets(pages, _CFO_KW, context=260, max_hits=40)
    if not hits:
        return {"status": "missing", "hits": []}
    changed = _proximity_change(hits, _LEAVE_KW)
    if changed:
        return {"status": "found", "count": len(changed), "hits": changed}
    return {"status": "clear", "hits": hits[:2]}


def extract_independent_director(pages: list[str]) -> dict:
    # 独立董事在治理章节出现频繁，上限放宽，避免漏掉后段"变动情况"章节
    hits = snippets(pages, ("独立董事",), context=240, max_hits=60)
    if not hits:
        return {"status": "missing", "hits": []}
    changed = _proximity_change(hits, ("辞职", "辞任", "辞去", "离任"))
    if changed:
        return {"status": "found", "count": len(changed), "hits": changed}
    return {"status": "clear", "hits": hits[:2]}


# ---------- 5.6 跨行业收购 ----------

def extract_cross_industry(pages: list[str]) -> dict:
    hits = snippets(pages, ("跨行业", "跨界收购", "投资状况分析", "收购"),
                    context=240, max_hits=12)
    if not hits:
        return {"status": "missing", "hits": []}
    # “跨行业”可能只是跨行业项目/客户描述，必须与收购语境同现才算信号
    cross = [h for h in hits
             if ("跨行业" in h["snippet"] or "跨界" in h["snippet"])
             and any(word in h["snippet"] for word in
                     ("收购", "并购", "股权收购", "资产收购", "标的公司"))]
    if cross:
        return {"status": "found", "count": len(cross), "hits": cross}
    return {"status": "ambiguous", "hits": hits[:3]}


# ---------- 5.9 监管处罚/立案调查 ----------

_REG_KW = ("立案", "警示函", "处罚决定", "监管措施", "行政处罚", "纪律处分",
           "通报批评", "公开谴责", "留置", "被调查", "受到处罚", "涉嫌违法违规",
           "监管函")
_POS_CHECK = re.compile(_CHECK_MARK + r"\s*是")
_NEG_CHECK = re.compile(_CHECK_MARK + r"\s*否")
_NEG_NEAR = re.compile(r"(?:未|不|无|否|零|0)\s*[^。；;\n]{0,8}"
                       r"(?:立案|警示函|处罚|监管|纪律|谴责|留置|调查)")
_ZERO_TIMES = re.compile(r"(?:0|零)\s*次")
_AMBIGUOUS = ("是否",)


def extract_regulatory(pages: list[str]) -> dict:
    """监管处罚/立案调查：按句判定勾选项/否定/主体，避免问答式误报。"""
    hits = snippets(pages, _REG_KW, context=260, max_hits=20)
    if not hits:
        return {"status": "clear", "hits": []}
    fails: list[dict] = []
    warns: list[dict] = []
    effective = 0
    for hit in hits:
        for sentence in _sentences(hit["snippet"]):
            if not any(word in sentence for word in _REG_KW):
                continue
            if any(word in sentence for word in _AMBIGUOUS) and not _POS_CHECK.search(sentence):
                continue  # 问句未勾选，不判
            if _NEG_CHECK.search(sentence) or _NEG_NEAR.search(sentence):
                continue
            if _ZERO_TIMES.search(sentence):
                continue
            if "环保" in sentence and ("罚款" in sentence or "合规整改" in sentence):
                continue  # 上游 C 类：一般性环保罚款/合规整改 → PASS
            effective += 1
            subject = any(word in sentence for word in
                          ("公司", "本公司", "控股股东", "实际控制人",
                           "董事", "监事", "高级管理人员"))
            if "立案" in sentence and subject and any(
                    word in sentence for word in ("被", "涉嫌", "遭")):
                fails.append({**hit, "snippet": sentence})
            elif any(word in sentence for word in _REG_KW[1:]):
                warns.append({**hit, "snippet": sentence})
    if fails:
        return {"status": "fail", "fails": fails, "warns": warns, "hits": hits}
    if warns:
        return {"status": "warn", "fails": [], "warns": warns, "hits": hits}
    if effective == 0:
        return {"status": "clear", "hits": hits}
    return {"status": "ambiguous", "hits": hits}


# ---------- 6.2 研发资本化比例 ----------

_RD_KW = ("研发投入资本化", "资本化研发投入", "研发投入合计", "资本化的比重",
          "资本化比重", "研发支出资本化", "资本化比例", "研发投入资本化率")
_UNIT_TO_YUAN = {"元": 1.0, "万元": 1e4, "亿元": 1e8}
_AMOUNT = re.compile(r"([\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)?")


def _amount_after(text: str, label: str) -> tuple[float, str] | None:
    match = re.search(label + r"[^0-9]{0,15}([\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)?", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "").replace("，", ""))
    return value, match.group(2) or ""


def _to_yuan(value: float, unit: str) -> float:
    return value * _UNIT_TO_YUAN.get(unit, 1.0)


def extract_rd_capitalization(pages: list[str]) -> dict:
    hits = snippets(pages, _RD_KW, context=300, max_hits=12)
    if not hits:
        return {"status": "missing", "hits": []}
    joined = "\n".join(h["snippet"] for h in hits)

    # 1) 金额除法：资本化金额 / 研发投入合计（或资本化/(资本化+费用化)）
    cap = _amount_after(joined, r"(?:本期)?资本化(?:研发)?投入(?:金额)?")
    total = _amount_after(joined, r"研发投入合计")
    expensed = _amount_after(joined, r"费用化研发投入")
    if cap and (total or expensed):
        cap_value = _to_yuan(*cap)
        if total:
            denom = _to_yuan(*total)
        else:
            denom = cap_value + _to_yuan(*expensed)
        if denom > 0 and 0 <= cap_value <= denom * 1.2:
            return {"status": "ok", "ratio": cap_value / denom,
                    "basis": "amount", "hits": hits}

    # 2) 百分比：标签后仅允许一个数值（多列相邻数值视为列错位，放弃）
    label = re.compile(
        r"(?:研发投入)?资本化(?:的)?(?:比重|比例|率)"
        r"\s*(?:[（(]\s*%\s*[)）])?\s*[:：]?\s*")
    for match in label.finditer(joined):
        tail = joined[match.end():match.end() + 30]
        tokens = re.findall(r"\d+(?:\.\d+)?", tail)
        if len(tokens) != 1:
            continue
        return {"status": "ok", "ratio": float(tokens[0]) / 100.0,
                "basis": "percent", "hits": hits}
    return {"status": "ambiguous",
            "reason": "检出研发投入表述，但未找到可绑定的资本化比例/金额",
            "hits": hits}


# ---------- 5.8 补充检查：非经营性占用 ----------

_OCCUPY_KW = ("非经营性占用", "非经营性资金占用")
_CHECK_POS = re.compile(_CHECK_MARK + r"\s*适用")
_CHECK_NEG = re.compile(_CHECK_MARK + r"\s*不适用")
_ANSWER_POS = re.compile(r"(?:占用|占用资金情况)[^。；;\n]{0,40}?(?:是|存在)")
_ANSWER_NEG = re.compile(
    r"(?:占用|占用资金情况)[^。；;\n]{0,40}?(?:否|无|不存在|未发生|不适用|不涉及)")
_PLAIN_NEG = re.compile(r"(?:不存在|未发生|不涉及)[^。；;\n]{0,20}占用")


def extract_non_operating_occupation(pages: list[str]) -> dict:
    """5.8 PDF 补充检查：解析"√适用/□不适用"勾选项，而非全窗搜子串。"""
    hits = snippets(pages, _OCCUPY_KW, context=300, max_hits=10)
    if not hits:
        return {"status": "missing", "hits": []}
    positives: list[dict] = []
    negatives = 0
    for hit in hits:
        for sentence in _sentences(hit["snippet"]):
            if not any(word in sentence for word in _OCCUPY_KW):
                continue
            if _CHECK_POS.search(sentence):
                positives.append({**hit, "snippet": sentence})
            elif _CHECK_NEG.search(sentence):
                negatives += 1
            elif _ANSWER_NEG.search(sentence) or _PLAIN_NEG.search(sentence):
                negatives += 1
            elif _ANSWER_POS.search(sentence):
                positives.append({**hit, "snippet": sentence})
    if positives:
        return {"status": "found", "hits": positives}
    if negatives:
        return {"status": "clear", "hits": hits}
    return {"status": "ambiguous", "hits": hits}
