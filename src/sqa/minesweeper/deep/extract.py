"""年报 PDF 文本证据抽取（深度模式）。

设计原则：只做"可验证的证据抽取"，不硬造判定——
- 数值型规则（运费/客户集中度/研发资本化）给出可复算的数值；
- 判断型规则（财务负责人/独董/收购/监管处罚）给出原文片段，判定交由 rules.py
  保守处理（证据不足时保持 SKIP，绝不猜测）。

2026-10-19 加固：保留换行以识别表格行边界；5.8/5.9 解析"√适用/√否"勾选项而非
全窗搜子串；5.5 支持汇总与明细跨页；6.2 绑定百分比标签/金额除法；5.3/5.4 按句
判定并扩大扫描窗口。

2026-10-25 复核修正：6.2 金额必须与标签同行（空白"资本化研发投入"单元格不再
串行取下一行"研发投入合计"金额），并支持用 合计−费用化 推导 0 资本化；5.9 跳过
"十、…情况/说明"式章节模板标题与"不适用"披露，修复睿创微纳式误报。
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
# 运费规则语境守卫（2026-10-25 复核修正）：运输费必须是成本/费用构成表中的项目，
# 不能来自关联方交易表（松发 603268 误取恒力海运运输费）。
# 注意：白酒等制造业的"运输费"在**营业成本构成表**（直接材料/直接人工/制造费用/
# 燃料动力/运输费/合计）中，表内不一定出现"营业成本"字样——若只认费用关键词会把
# 茅台 600519 这类正例误判为 SKIP，故增加"成本构成表行"启发式（行首为费用项目且
# 同段含合计行）。
_FREIGHT_EXPENSE_CONTEXT = ("销售费用", "营业成本", "主营业务成本", "成本构成",
                            "管理费用", "附注")
_FREIGHT_EXCLUDE_CONTEXT = ("关联方", "关联交易", "关联方交易", "接受劳务",
                            "采购商品", "采购总额")


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
    """从销售费用/营业成本（含成本构成表）附注中抽取运输费本期与上期金额。

    2026-10-25 复核修正：命中片段必须处于费用/成本语境（或成本构成表行：行首为
    费用项目且同段有合计），且不得来自关联方交易表——否则会取到"恒力海运（大连）
    运输费"这类关联采购金额（松发 603268 误报 FAIL 的根因）；同时兼容白酒等制造业
    把运输费列入"营业成本构成表"（茅台 600519 p11）而表内不出现"销售费用"的版式。
    """
    hits = snippets(pages, _FREIGHT_KW, context=240, max_hits=12)
    if not hits:
        return {"status": "missing", "reason": "附注未单独披露运输费（新准则下常计入营业成本）",
                "hits": []}
    for hit in hits:
        snippet = hit["snippet"]
        if any(word in snippet for word in _FREIGHT_EXCLUDE_CONTEXT):
            continue  # 关联方交易表/采购表不是成本费用附注
        lines = snippet.splitlines()
        expense_context = any(word in snippet for word in _FREIGHT_EXPENSE_CONTEXT)
        for idx, line in enumerate(lines):
            if hit["keyword"] not in line:
                continue
            # 成本构成表行：行首即费用项目名，且同段含"合计"行（茅台营业成本构成表）；
            # 关联方表行通常以公司名开头，已在上方被排除。
            table_row = (line.strip().startswith(hit["keyword"])
                         and "合计" in snippet)
            if not (expense_context or table_row):
                continue  # 无法确认成本/费用语境：宁漏勿错
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
    """独立董事辞职：只认"辞职/辞任/辞去"，排除换届/任期届满的离任。

    2026-10-25 复核修正：原实现把「离任」当变动词并逐窗口累加，松发 603268
    借壳后提前换届（3 名独董离任、同日新聘 3 名）被误报为「30 处集体辞职」。
    现按（页, 句子）去重，并排除换届/换届选举语境。
    """
    names_kw = ("辞职", "辞任", "辞去")
    hits = snippets(pages, ("独立董事",), context=240, max_hits=60)
    if not hits:
        return {"status": "missing", "hits": []}
    changed: list[dict] = []
    seen: set[tuple[int, str]] = set()
    count = 0
    for hit in hits:
        for sentence in _sentences(hit["snippet"]):
            if "独立董事" not in sentence:
                continue
            if not any(word in sentence for word in names_kw):
                continue
            if any(word in sentence for word in ("换届", "任期届满", "提前换届")):
                continue  # 换届/届满导致的离任不是"辞职"
            key = (int(hit["page"]), sentence.strip())
            if key in seen:
                continue  # 同一句被重叠窗口重复命中：去重
            seen.add(key)
            changed.append({**hit, "snippet": sentence})
            count += max(1, sentence.count("独立董事"))
    if changed:
        return {"status": "found", "count": count, "hits": changed}
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
#
# 2026-10-25 复核修正（修复 5 起确证误报）：
# 1. PDF 硬换行会把否定句拆断（"未收到被\n监管部门采取行政监管措施"、"不存在因
#    涉嫌…\n被立案调查"），按 \n 切句后否定语境丢失 → 先把页内断行还原为语义块；
# 2. 否定窗口原先仅 8 字符，长定语否定句（"不存在因涉嫌与本次交易相关的内幕交易
#    被立案调查"）失效 → 改为同分句内 40 字符窗口 + 全句"不存在……的情形"兜底，
#    并排除"但/然而"转折与跨逗号否定（"不存在违规但被立案"仍判 FAIL）；
# 3. 承诺函/补偿条款中的假设句（"如因…遭受损失（包括受到行政处罚…）"、"自愿接受
#    …监管措施"）不是处罚事实 → 只认关键词**之前**的假设引导词，避免真处罚句
#    尾部"如果…"被误杀；
# 4. 章节模板标题（"十、…受到处罚\n及整改情况\n□适用 √不适用"）断行后"及整改"
#    跑到下一行，原守卫误判为事实 → 合并断行后再判标题；
# 5. 表格披露要求文案（"报告期内…因环境问题受到行政处罚的情况…（如有）"）不是
#    事实 → 增加"是否/不适用"局部上下文与"主体+被/收到/受到/存在"事实要件
#    （主体必须在关键词之前）；页眉只剥离页眉片段、保留拼行正文。

_REG_KW = ("立案", "警示函", "处罚决定", "监管措施", "行政处罚", "纪律处分",
           "通报批评", "公开谴责", "留置", "被调查", "受到处罚", "涉嫌违法违规",
           "监管函", "被处罚")
_POS_CHECK = re.compile(_CHECK_MARK + r"\s*是")
_POS_APPLY = re.compile(_CHECK_MARK + r"\s*适用")
_ZERO_TIMES = re.compile(r"(?:0|零)\s*次")
_MARK_CHARS = "√☑☒✓✔■"
_CHECKBOX_PHRASE = re.compile(r"[□√☑☒✓✔■]\s*(?:适用|不适用)")
# 年报章节模板标题（如"十、…涉嫌违法违规、受到处罚及整改情况"）：关键词命中但
# 只是目录/标题文字，实际内容在下一行勾选"不适用"，不得当作处罚事实。
_SECTION_ORDINAL = re.compile(r"^[（(]?[一二三四五六七八九十百零〇]+[）)、.．]")
_SECTION_TAIL = re.compile(r"(?:情况|说明|事项)[。：:]?$")
_SECTION_HINTS = ("整改", "情况", "说明", "事项")
# 标题守卫：出现处罚事实/具体处罚类型措辞时，即便以中文序号开头也按正文处理，
# 避免把编号段落中的真实处罚当成章节标题跳过（如"（一）公司受到深交所通报批评"）。
_GENUINE_MARKERS = ("被", "收到", "立案", "罚款", "留置", "调查", "存在",
                    "行政处罚", "通报批评", "公开谴责", "纪律处分",
                    "监管措施", "警示函", "监管函", "处罚决定")
# 断行还原：结构性行（中文/阿拉伯编号、勾选项、项目符号）另起一块，其余续行合并。
_STRUCT_LINE = re.compile(
    r"^(?:[（(]?(?:[一二三四五六七八九十百零〇]+|\d+)[）)、.．]"
    r"|第[一二三四五六七八九十百零〇]+[章节条]"
    r"|[□√☑☒✓✔■]"
    r"|[-−—–•·])"
)
_SENT_END = re.compile(r"[。．！？；;：:]$")
# 页眉/页脚（"…2025 年年度报告98 / 232"）：不并入正文语义块，避免把"处罚"等词与
# 公司名拼成假事实。
_PAGE_HEADER = re.compile(r"(?:年度报告|半年报告|年报).{0,8}\d+\s*/\s*\d+"
                          r"|^\s*\d+\s*/\s*\d+\s*$")
# 处罚事实要件：主体 + 被动/承受类动词（防止表格字段名、风险表述被误判）。
_SUBJECT = re.compile(r"(?:公司|本公司|上市公司|控股股东|实际控制人|董事|监事|"
                      r"高级管理人员|董监高|高管)")
_PENALTY_VERB = re.compile(r"(?:被|收到|受到|遭到|存在)")
# 假设/补偿语境（承诺函、兜底条款）：不是已发生的处罚事实。
# 假设引导词只认关键词**之前**（120 字符内）出现，避免"公司被立案调查，如果最终
# 认定违规…"这类真处罚句被后置的"如果"误杀；补偿/损失措辞需有前置假设词搭配。
_HYPOTHETICAL_LEAD = re.compile(
    r"(?:如因|若因|如果|一旦|倘若|如本次|如本人|如本企业|如公司|如本|自愿接受)")
_HYPOTHETICAL_TAIL = re.compile(
    r"(?:遭受损失|赔偿安排|予以赔偿|予以补偿|承担相应的补偿)")
_NEG_SCOPE = re.compile(r"(?:不存在|均不存在|未发生|未收到|未受到|未出现)")
_SCOPE_TAIL = re.compile(r"(?:情形|情况|事项|案件|行为|记录)[。]?$")
# 风险描述（"…行政处罚风险明显增加"）不是处罚事实。
_RISK_AFTER_KW = re.compile(r"[^。；;\n]{0,6}风险")


def _merge_page_blocks(page_text: str) -> list[str]:
    """把 PDF 页内硬换行还原为语义块，便于识别跨行否定句/标题/表格字段。"""
    blocks: list[str] = []
    current = ""
    for raw in page_text.splitlines():
        line = re.sub(r"[ \t\u3000]+", " ", raw).strip()
        if not line:
            if current:
                blocks.append(current)
                current = ""
            continue
        header = _PAGE_HEADER.search(line) if len(line) < 120 else None
        if header is not None:
            # 页眉/页脚：只剥离页眉片段，保留可能被 pypdf 拼到同一行的正文
            # （否则真实处罚句可能随页眉一起被丢弃）。
            if current:
                blocks.append(current)
                current = ""
            remainder = line[header.end():].strip()
            if remainder:
                current = remainder
            continue
        if current and not _SENT_END.search(current) and not _STRUCT_LINE.match(line):
            current += line
            continue
        if current:
            blocks.append(current)
        current = line
    if current:
        blocks.append(current)
    return blocks


def _split_block_sentences(block: str) -> list[str]:
    """句级切分：只用句末标点（不再用 \\n，避免否定语境被硬换行切断）。"""
    return [s.strip() for s in re.split(r"[。．！？；;]", block) if s.strip()]


def _has_genuine_penalty_fact(sentence: str) -> bool:
    """句中是否出现具体处罚事实/类型（用于区分章节模板标题与真实处罚）。"""
    if any(word in sentence for word in _GENUINE_MARKERS):
        return True
    # 模板标题固定为"受到处罚及整改情况"；没有"及整改"的"受到处罚"按事实处理
    return "受到处罚" in sentence and "及整改" not in sentence


def _is_regulatory_section_header(sentence: str) -> bool:
    if not _SECTION_ORDINAL.match(sentence):
        return False
    if any(ch in sentence for ch in _MARK_CHARS):
        return False  # 勾选项出现在同一行：交给勾选/否定逻辑判定
    if _has_genuine_penalty_fact(sentence):
        return False
    if _SECTION_TAIL.search(sentence):
        return True
    return any(hint in sentence for hint in _SECTION_HINTS)


def _hypothetical(sentence: str, pos: int) -> bool:
    """假设/补偿语境：关键词之前有假设引导词，或"前有假设词 + 本句补偿/损失措辞"。"""
    lead = sentence[max(0, pos - 120):pos]
    if _HYPOTHETICAL_LEAD.search(lead):
        return True
    tail = _HYPOTHETICAL_TAIL.search(sentence)
    if tail is not None and re.search(r"(?:如|若|一旦|倘若)", sentence[:tail.start()]):
        return True
    return False


def _negated(sentence: str, pos: int) -> bool:
    """关键词前同一分句内是否有否定词（跨行断句已由块合并修复）。

    先剔除"□适用 / √不适用"勾选项短语：勾选符号后的"不适用"是披露选项，
    不是对后续事实句的否定（600519「√适用 □不适用详见…高管被实施留置…」）。
    再看最后一个否定词到关键词的距离（≤40 字符）并排除"但/然而"转折——
    "不存在违规行为但控股股东被立案调查"中的否定不覆盖后一分句。
    """
    prefix = sentence[max(0, pos - 45):pos]
    prefix = _CHECKBOX_PHRASE.sub("", prefix)
    clause = re.split(r"[，,。．；;！？]", prefix)[-1]
    matches = list(re.finditer(r"(?:未|不|无|否|零|尚未|未经)", clause))
    if not matches:
        return False
    gap = clause[matches[-1].end():]
    if len(gap) > 40:
        return False
    return not any(word in gap for word in
                   ("但", "然而", "不过", "却", "仍", "且", "并且"))


def _negated_scope(sentence: str, pos: int) -> bool:
    """否定覆盖整句："不存在……的情形/情况/事项"（否定词离关键词较远时兜底）。

    否定词与关键词之间不能有逗号/分号或转折词，否则否定只作用于前一分句
    （"不存在违规行为，但控股股东被立案调查"应判 FAIL）。
    """
    if not _SCOPE_TAIL.search(sentence):
        return False
    for match in _NEG_SCOPE.finditer(sentence):
        if match.start() > pos:
            continue
        gap = sentence[match.end():pos]
        if re.search(r"[，,；;]", gap):
            continue
        if any(word in gap for word in
               ("但", "然而", "不过", "却", "仍", "且", "并且")):
            continue
        return True
    return False


def _has_penalty_fact(sentence: str, pos: int, keyword: str) -> bool:
    """是否有"主体 + 被/收到/受到/存在"的处罚事实要件。

    主体必须在关键词之前（"公司收到…""控股股东被…"）；不接受关键词之后出现的
    主体——否则"受到处罚的上市公司应当披露…"这类制度性表述会被误当成事实。
    动词只认关键词之前或关键词本身（"被处罚""收到警示函"），不接受关键词之后
    的"存在"——否则"…不存在……诉讼、仲裁、行政处罚"会被"不存在"误当成事实。
    """
    left = sentence[max(0, pos - 45):pos]
    if not _SUBJECT.search(left):
        return False
    return bool(_PENALTY_VERB.search(left) or _PENALTY_VERB.match(keyword))


def _skip_occurrence(sentence: str, pos: int, keyword: str) -> bool:
    """单次关键词命中的过滤：标题/否定/假设/风险/问答/表格字段/环保豁免。"""
    local = sentence[max(0, pos - 60):pos + len(keyword) + 60]
    if _RISK_AFTER_KW.search(sentence[pos + len(keyword):pos + len(keyword) + 10]):
        return True  # "行政处罚风险…"式风险描述
    if _hypothetical(sentence, pos):
        return True  # 承诺函/补偿条款/假设句
    if _is_regulatory_section_header(sentence):
        return True  # "十、…及整改情况"式章节模板标题
    if ("不适用" in local and not _POS_APPLY.search(local)
            and not _has_genuine_penalty_fact(sentence)):
        return True  # "□适用 √不适用"式披露：该事项不适用
    if "是否" in local and not _POS_CHECK.search(local):
        return True  # 问句/表格字段未勾选，不判
    if _negated(sentence, pos) or _negated_scope(sentence, pos):
        return True
    if _ZERO_TIMES.search(local):
        return True
    if "环保" in sentence and ("罚款" in sentence or "合规整改" in sentence):
        return True  # 上游 C 类：一般性环保罚款/合规整改 → PASS
    return not _has_penalty_fact(sentence, pos, keyword)


def extract_regulatory(pages: list[str]) -> dict:
    """监管处罚/立案调查：按语义块/句判定勾选项、否定、假设与主体。"""
    raw_hits: list[dict] = []
    fails: list[dict] = []
    warns: list[dict] = []
    effective = 0
    for page_no, page in enumerate(pages, start=1):
        if not page:
            continue
        for block in _merge_page_blocks(page):
            if not any(word in block for word in _REG_KW):
                continue
            for sentence in _split_block_sentences(block):
                if not any(word in sentence for word in _REG_KW):
                    continue
                first_kw = next(word for word in _REG_KW if word in sentence)
                if len(raw_hits) < 20:
                    raw_hits.append({"page": page_no, "keyword": first_kw,
                                     "snippet": sentence})
                matched = None
                for keyword in _REG_KW:
                    start = 0
                    while True:
                        pos = sentence.find(keyword, start)
                        if pos < 0:
                            break
                        start = pos + len(keyword)
                        if not _skip_occurrence(sentence, pos, keyword):
                            matched = (keyword, pos)
                            break
                    if matched:
                        break
                if not matched:
                    continue
                keyword, pos = matched
                effective += 1
                hit = {"page": page_no, "keyword": keyword, "snippet": sentence}
                if "立案" in sentence and _SUBJECT.search(
                        sentence[max(0, pos - 45):pos + len(keyword) + 12]) and any(
                            word in sentence for word in ("被", "涉嫌", "遭")):
                    fails.append(hit)
                else:
                    warns.append(hit)
    if fails:
        return {"status": "fail", "fails": fails, "warns": warns, "hits": raw_hits}
    if warns:
        return {"status": "warn", "fails": [], "warns": warns, "hits": raw_hits}
    if effective == 0:
        return {"status": "clear", "hits": raw_hits}
    return {"status": "ambiguous", "hits": raw_hits}


# ---------- 6.2 研发资本化比例 ----------

_RD_KW = ("研发投入资本化", "资本化研发投入", "研发投入合计", "资本化的比重",
          "资本化比重", "研发支出资本化", "资本化比例", "研发投入资本化率")
_UNIT_TO_YUAN = {"元": 1.0, "万元": 1e4, "亿元": 1e8}
_AMOUNT = re.compile(r"([-+]?[\d,，]+(?:\.\d+)?)\s*(亿元|万元|元)?")
_CJK = re.compile(r"[\u4e00-\u9fff]")
# 续行仅接受纯数字（不含 %：百分比行属于占比，不是金额）
_RESIDUAL_LINE = re.compile(r"^[\s\d,，.。()（）元万亿+\-]+$")
# 资本化金额标签；排除"…占比/比重/比例/率"，避免把百分比行当金额。
_CAP_LABEL = re.compile(
    r"(?:(?:本期)?资本化(?:研发)?投入(?:金额)?|研发投入资本化(?:金额)?)"
    r"(?!\s*(?:的)?\s*(?:占比|比重|比例|率))")
_TOTAL_LABEL = re.compile(r"研发投入合计(?!\s*(?:的)?\s*(?:占比|比重|比例|率))")
_EXP_LABEL = re.compile(
    r"费用化研发(?:投入|支出)(?!\s*(?:的)?\s*(?:占比|比重|比例|率))")
_PCT_LABEL = re.compile(
    r"(?:研发投入)?资本化(?:研发)?(?:投入)?(?:的)?(?:占比|比重|比例|率)"
    r"\s*(?:[（(]\s*%\s*[)）])?\s*[:：]?\s*")


def _amount_on_line(line: str, label: re.Pattern) -> tuple[float, str] | None:
    """同一行内、标签之后提取金额（不允许跨行，防止空白单元格串行）。"""
    match = label.search(line)
    if not match:
        return None
    tail = line[match.end():]
    number = _AMOUNT.search(tail)
    if not number:
        return None
    if tail[number.end():].lstrip().startswith("%"):
        return None  # 百分比行不是金额
    return (float(number.group(1).replace(",", "").replace("，", "")),
            number.group(2) or "")


def _amount_with_continuation(lines: list[str],
                              label: re.Pattern) -> tuple[float, str] | None:
    """标签行无金额时，仅接受下一行"纯数字续行"（pypdf 常见版式）。

    下一行若出现中文字段名（如"研发投入合计"），说明是另一表项，拒绝拼接
    ——这是"空白单元格 + 下一行金额"误配的修复点；标签行含"占比/%"时不续行
    （续行数字是比例，不是金额）。
    """
    for idx, line in enumerate(lines):
        found = _amount_on_line(line, label)
        if found is not None:
            return found
        match = label.search(line)
        if not match or idx + 1 >= len(lines):
            continue
        tail = line[match.end():]
        if "%" in tail or any(key in tail for key in ("占比", "比重", "比例", "率")):
            continue  # 百分比行：续行数字是比例，不是金额
        nxt = lines[idx + 1].strip()
        if nxt and not _CJK.search(nxt) and _RESIDUAL_LINE.match(nxt):
            number = _AMOUNT.match(nxt)
            if number:
                return (float(number.group(1).replace(",", "").replace("，", "")),
                        number.group(2) or "")
    return None


def _to_yuan(value: float, unit: str) -> float:
    return value * _UNIT_TO_YUAN.get(unit, 1.0)


def extract_rd_capitalization(pages: list[str]) -> dict:
    """研发资本化比例（2026-10-25 复核修正）。

    取数优先级：
    1) 资本化金额 ÷ 研发投入合计（同一行绑定）；
    2) 资本化金额缺失（空白单元格常表示 0）时，用 研发投入合计 − 费用化研发投入
       推导资本化金额——避免把下一行"研发投入合计"的金额误当资本化金额；
    3) 资本化金额 ÷（资本化 + 费用化）；
    4) 百分比标签同行仅一个数值。
    """
    hits = snippets(pages, _RD_KW, context=300, max_hits=12)
    if not hits:
        return {"status": "missing", "hits": []}
    lines: list[str] = []
    for hit in hits:
        lines.extend(hit["snippet"].splitlines())

    cap = _amount_with_continuation(lines, _CAP_LABEL)
    total = _amount_with_continuation(lines, _TOTAL_LABEL)
    expensed = _amount_with_continuation(lines, _EXP_LABEL)

    # 金额不允许为负/合计必须为正（报表口径异常或符号解析错误）：按缺失处理，不猜测
    if cap is not None and cap[0] < 0:
        cap = None
    if expensed is not None and expensed[0] < 0:
        expensed = None
    if total is not None and total[0] <= 0:
        total = None

    def _same_scale(a: tuple[float, str] | None,
                    b: tuple[float, str] | None) -> bool:
        """金额口径要求同一量级单位；同表混用单位（一侧亿元一侧无单位）视为不可靠。"""
        if a is None or b is None:
            return False
        return (a[1] or "元") == (b[1] or "元")

    cap_value = _to_yuan(*cap) if cap else None
    total_value = _to_yuan(*total) if total else None
    exp_value = _to_yuan(*expensed) if expensed else None

    cap_total_unconfirmed = False
    if total_value and total_value > 0:
        # 1) 资本化金额 ÷ 研发投入合计（同行绑定 + 单位一致）
        if (cap is not None and _same_scale(cap, total)
                and 0 <= cap_value <= total_value * 1.2):
            if (expensed is None
                    and abs(cap_value - total_value) <= total_value * 1e-9):
                # 金额恰好相等且费用化缺失：无法区分"全额资本化"与解析错位，
                # 先继续尝试百分比证据，找不到再返回 ambiguous
                cap_total_unconfirmed = True
            else:
                consistent = not (exp_value and exp_value > 0
                                  and abs(cap_value - total_value) <= total_value * 1e-6)
                if consistent:
                    return {"status": "ok", "ratio": cap_value / total_value,
                            "basis": "amount", "hits": hits}
        # 2) 仅在资本化金额缺失时：研发投入合计 − 费用化研发投入
        if (cap is None and _same_scale(expensed, total)
                and 0 <= exp_value <= total_value * 1.2):
            derived = total_value - exp_value
            if 0 <= derived <= total_value * 1.2:
                return {"status": "ok", "ratio": max(derived, 0.0) / total_value,
                        "basis": "amount_derived", "hits": hits}
    # 3) 资本化 ÷（资本化 + 费用化）
    if (_same_scale(cap, expensed)
            and cap_value is not None and exp_value is not None
            and cap_value + exp_value > 0):
        return {"status": "ok", "ratio": cap_value / (cap_value + exp_value),
                "basis": "amount", "hits": hits}

    # 4) 百分比：标签后同行仅允许一个数值（多列相邻数值视为列错位，放弃）；
    #    标签行无值时允许下一行纯数字续行（pypdf 常见版式）
    for idx, line in enumerate(lines):
        match = _PCT_LABEL.search(line)
        if not match:
            continue
        tokens = re.findall(r"\d+(?:\.\d+)?", line[match.end():])
        if len(tokens) == 1:
            ratio = float(tokens[0]) / 100.0
            if 0.0 <= ratio <= 1.0:
                return {"status": "ok", "ratio": ratio, "basis": "percent", "hits": hits}
        elif not tokens and idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if nxt and not _CJK.search(nxt) and _RESIDUAL_LINE.match(nxt):
                next_tokens = re.findall(r"\d+(?:\.\d+)?", nxt)
                if len(next_tokens) == 1:
                    ratio = float(next_tokens[0]) / 100.0
                    if 0.0 <= ratio <= 1.0:
                        return {"status": "ok", "ratio": ratio,
                                "basis": "percent", "hits": hits}
    if cap_total_unconfirmed:
        return {"status": "ambiguous",
                "reason": "资本化金额与研发投入合计相等且未披露费用化金额，无法确认",
                "hits": hits}
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

# ---------- 5.2 大股东减持：非减持事件披露（补充覆盖） ----------

_HOLDER_EVENT_KW = ("无偿划转", "可交换债", "可交换公司债")
_HOLDER_EVENT_CONTEXT = ("持股比例", "持股数量", "部分持股", "持股减少",
                         "股份减少", "主要原因", "持股变动")


def extract_holder_events(pages: list[str]) -> dict:
    """5.2 补充检查：年报是否披露"大股东持股减少系无偿划转/可交换债换股"。

    长江电力 600900 实盘：p57 脚注「控股股东部分持股减少，主要原因一是 …股
    份无偿划转至全资子公司；二是 …可交换债券进入换股期」——这类减少不属于
    二级市场减持，但纯结构化数据（比例/股数）无法区分。仅在"划转/EB 披露"与
    "持股减少"同段出现时判定为 found，供 rule_5_2 覆盖为 PASS。
    """
    hits = snippets(pages, _HOLDER_EVENT_KW, context=320, max_hits=20)
    if not hits:
        return {"status": "missing", "hits": []}
    matched = [h for h in hits
               if any(kw in h["snippet"] for kw in _HOLDER_EVENT_CONTEXT)]
    if matched:
        return {"status": "found", "hits": matched}
    return {"status": "clear", "hits": hits}
