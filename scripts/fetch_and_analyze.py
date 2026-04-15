#!/usr/bin/env python3
"""
港股IPO自动分析脚本
- 抓取港交所主板新上市信息
- 下载并解析招股书PDF（pdfplumber）
- 规则打分（4维度×25分=100分）
- 更新 data.json
- 飞书通知
"""

import json
import os
import re
import sys
import time
import datetime
import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
HKEX_URL = "https://www2.hkexnews.hk/New-Listings/New-Listing-Information/Main-Board?sc_lang=zh-HK"
HKEX_ANNOUNCE_URL = "https://www2.hkexnews.hk/search/titlesearch.xhtml?lang=ZH"
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
DATA_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}


# ─────────────────────────────────────────────
# 1. 抓取港交所新股列表
# ─────────────────────────────────────────────
def fetch_hkex_listings():
    """返回 [{'code': '02476', 'name': '...', 'listDate': '2026-04-21'}, ...]"""
    listings = []
    try:
        resp = requests.get(HKEX_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # 主策略：从页面纯文本用正则提取「代码 + 公司名」
        # HKEX 页面实际结构为换行分隔的文本，不用标准 <table>
        page_text = soup.get_text()
        # 匹配：4-5位数字 换行 公司名（支持中英文）
        matches = re.findall(
            r"(\d{4,5})\s*\n\s*([^\n]{4,60}(?:有限公司|Inc\.|Corp\.|Ltd\.|Limited))",
            page_text,
        )
        seen = set()
        for code_raw, name in matches:
            code = code_raw.zfill(5)
            if code not in seen:
                seen.add(code)
                listings.append({"code": code, "name": name.strip(), "listDate": ""})

        # 备用策略：若主策略失败，尝试表格解析
        if not listings:
            for table in soup.find_all("table"):
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 3:
                        continue
                    text_cells = [c.get_text(strip=True) for c in cells]
                    code = None
                    list_date = ""
                    for cell in text_cells:
                        m = re.match(r"^(\d{4,5})$", cell)
                        if m:
                            code = m.group(1).zfill(5)
                        dm = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[/-]\d{2}[/-]\d{4})", cell)
                        if dm:
                            list_date = _normalize_date(dm.group(1))
                    if code and code not in seen:
                        seen.add(code)
                        name = max(
                            [c for c in text_cells if c != code and not re.match(r"\d{1,2}[/-]", c)],
                            key=len, default="",
                        )
                        listings.append({"code": code, "name": name, "listDate": list_date})

        # 最后兜底：只提取5位数字代码
        if not listings:
            codes = re.findall(r"\b(\d{5})\b", resp.text)
            for c in dict.fromkeys(codes):
                listings.append({"code": c, "name": "", "listDate": ""})

    except Exception as e:
        print(f"[WARN] fetch_hkex_listings failed: {e}")

    return listings


def _normalize_date(raw):
    """统一转成 YYYY-MM-DD"""
    raw = raw.replace("/", "-")
    parts = raw.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 4:
            return raw  # already YYYY-MM-DD
        if len(parts[2]) == 4:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return raw


# ─────────────────────────────────────────────
# 2. 搜索招股书 PDF 链接
# ─────────────────────────────────────────────
def search_prospectus_url(stock_code):
    """在港交所公告搜索招股书，返回PDF直链（可能为None）"""
    try:
        url = (
            "https://www1.hkexnews.hk/search/titlesearch.xhtml"
            f"?lang=ZH&market=SEHK&searchType=0&documentType=-1"
            f"&returnType=0&t1code=-2&t2Gcode=-2&t2code=-2&stockId={stock_code}"
            f"&from=&to=&title=招股章程&addKeyword=&search=Search"
        )
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf") and ("prospectus" in href.lower() or "招股" in a.get_text()):
                if not href.startswith("http"):
                    href = "https://www1.hkexnews.hk" + href
                return href
    except Exception as e:
        print(f"[WARN] search_prospectus_url({stock_code}): {e}")
    return None


# ─────────────────────────────────────────────
# 3. 下载 PDF
# ─────────────────────────────────────────────
def download_pdf(url, max_mb=30):
    """流式下载PDF，超过 max_mb 则截断返回已下载部分"""
    try:
        resp = requests.get(url, headers=HEADERS, stream=True, timeout=60)
        resp.raise_for_status()
        chunks = []
        total = 0
        limit = max_mb * 1024 * 1024
        for chunk in resp.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
        return b"".join(chunks)
    except Exception as e:
        print(f"[WARN] download_pdf({url}): {e}")
    return None


# ─────────────────────────────────────────────
# 4. 从 PDF 提取财务指标
# ─────────────────────────────────────────────
def _find_numbers(text, pattern, group=1, scale=1.0, many=False):
    """用正则在文本中找数字，返回float列表或单个float"""
    results = []
    for m in re.finditer(pattern, text, re.IGNORECASE):
        raw = m.group(group).replace(",", "").replace("，", "")
        try:
            results.append(float(raw) * scale)
        except ValueError:
            pass
    if many:
        return results
    return results[0] if results else None


def extract_financials(pdf_bytes):
    """
    从 PDF 字节流提取关键指标，返回 dict。
    若 pdfplumber 未安装或解析失败，返回空 dict。
    """
    if pdfplumber is None or pdf_bytes is None:
        return {}

    import io
    data = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # 只取前40页（招股书摘要通常在前部）
            pages = pdf.pages[:40]
            text = "\n".join(p.extract_text() or "" for p in pages)

        # ── 发行价
        price = _find_numbers(
            text,
            r"发[售行]价[^0-9]{0,10}([\d,]+\.?\d*)\s*港元",
        )
        if not price:
            price = _find_numbers(
                text,
                r"HK\$\s*([\d,]+\.?\d*)\s*(?:per|每)",
            )
        data["price"] = price

        # ── 每手股数
        lot = _find_numbers(text, r"每手[^0-9]{0,5}([\d,]+)\s*股")
        data["lotSize"] = lot

        # ── 收入（人民币/港元，百万/千元）—— 取最近3年
        rev_list = _find_numbers(
            text,
            r"收[入益][^\n]{0,30}([\d,]+(?:\.\d+)?)\s*(?:百万|千万)?",
            many=True,
        )
        data["revenues"] = rev_list[:3] if rev_list else []

        # ── 毛利率（%）
        gm_list = _find_numbers(
            text,
            r"毛利率[^\n]{0,20}([\d]+\.?\d*)\s*%",
            many=True,
        )
        data["grossMargins"] = gm_list[:3] if gm_list else []

        # ── 净利润
        np_list = _find_numbers(
            text,
            r"(?:年内利润|净利润)[^\n]{0,20}([\d,]+(?:\.\d+)?)",
            many=True,
        )
        data["netProfits"] = np_list[:3] if np_list else []

        # ── 经营活动现金流
        ocf_list = _find_numbers(
            text,
            r"经营活动[^\n]{0,10}([\d,]+(?:\.\d+)?)",
            many=True,
        )
        data["ocfs"] = ocf_list[:3] if ocf_list else []

        # ── 基石投资者占比
        cornerstone = _find_numbers(
            text,
            r"基石投资者[^\n]{0,30}([\d]+\.?\d*)\s*%",
        )
        data["cornerstonePct"] = cornerstone

        # ── 上市日期
        ld = re.search(r"上市日期[^\d]{0,10}(\d{4}年\d{1,2}月\d{1,2}日)", text)
        if ld:
            raw = ld.group(1)
            raw = re.sub(r"年|月", "-", raw).replace("日", "")
            parts = [p.zfill(2) for p in raw.split("-")]
            data["listDate"] = "-".join(parts)

    except Exception as e:
        print(f"[WARN] extract_financials: {e}")

    return data


# ─────────────────────────────────────────────
# 5. 规则打分
# ─────────────────────────────────────────────
def calculate_score(fin):
    """
    4维度×25分=100分
    fin: extract_financials() 返回的 dict
    返回 (scores_dict, total, verdict, verdict_label)
    """
    scores = {}

    # ── 业务质量（0-25）
    biz = 12  # 基础分
    revenues = fin.get("revenues", [])
    if len(revenues) >= 2 and revenues[-2] > 0:
        growth = (revenues[-1] - revenues[-2]) / revenues[-2] * 100
        if growth > 50:
            biz += 5
        elif growth > 20:
            biz += 3
    gms = fin.get("grossMargins", [])
    if gms:
        gm = gms[-1]
        if gm > 60:
            biz += 5
        elif gm > 40:
            biz += 3
    nps = fin.get("netProfits", [])
    if nps and nps[-1] > 0:
        biz += 3
    scores["business"] = min(25, biz)

    # ── 财务健康（0-25）
    fin_s = 10
    if nps and nps[-1] > 0:
        fin_s += 4
    ocfs = fin.get("ocfs", [])
    if ocfs and ocfs[-1] > 0:
        fin_s += 3
        if nps and nps[-1] > 0:
            ratio = ocfs[-1] / nps[-1]
            if ratio >= 0.8:
                fin_s += 4
    revs = fin.get("revenues", [])
    if revs and revs[-1] > 0 and nps:
        nm = nps[-1] / revs[-1] * 100
        if nm > 25:
            fin_s += 4
    scores["financial"] = min(25, fin_s)

    # ── 估值吸引力（0-25）基于PE粗估
    val = 12
    price = fin.get("price")
    lot = fin.get("lotSize")
    if price and lot and nps and len(revenues) > 0:
        # 非常粗略的PE：假设总股本无法精确获取，用数据范围判断
        # 如果毛利率高（>60%）且净利率高（>25%），给正面加分
        if gms and gms[-1] > 60:
            val += 3
        if gms and gms[-1] > 40:
            val += 2
    scores["valuation"] = min(25, val)

    # ── 资本结构（0-25）
    cap = 10
    corner = fin.get("cornerstonePct")
    if corner:
        if corner > 40:
            cap += 10
        elif corner > 30:
            cap += 7
        elif corner > 20:
            cap += 5
        elif corner > 10:
            cap += 2
    # 全部新股+3
    cap += 3
    scores["capital"] = min(25, cap)

    total = sum(scores.values())

    if total >= 70:
        verdict, label = "da", "打"
    elif total >= 50:
        verdict, label = "watch", "观望"
    else:
        verdict, label = "noda", "不打"

    return scores, total, verdict, label


# ─────────────────────────────────────────────
# 6. 构建 stock entry（JSON格式）
# ─────────────────────────────────────────────
def build_stock_entry(code, name, fin, scores, total, verdict, verdict_label):
    price = fin.get("price") or 0.0
    lot = fin.get("lotSize") or 100
    entry_fee = round(price * lot * 1.01005, 2) if price else None

    revenues = fin.get("revenues", [])
    gms = fin.get("grossMargins", [])
    nps = fin.get("netProfits", [])
    ocfs = fin.get("ocfs", [])

    def pct(v): return f"{v:.1f}%" if v else "—"
    def amt(v): return f"{v/1e6:.1f}亿" if v and v >= 1e6 else (f"{v:.1f}M" if v else "—")

    rev_growth = "—"
    if len(revenues) >= 2 and revenues[-2] > 0:
        g = (revenues[-1] - revenues[-2]) / revenues[-2] * 100
        rev_growth = f"+{g:.1f}%" if g >= 0 else f"{g:.1f}%"

    # 结论文本
    gm_str = pct(gms[-1]) if gms else "—"
    conclusion = (
        f"自动分析 | 近期上市新股。"
        f"毛利率 {gm_str}，收入增速 {rev_growth}。"
        f"基本面评分 {total}/100，建议：{verdict_label}。"
        f"（本分析由规则引擎自动生成，仅供参考）"
    )

    score_breakdown = [
        {"label": "业务质量", "pts": scores["business"], "max": 25, "desc": "自动评分：收入增速+毛利率"},
        {"label": "财务健康", "pts": scores["financial"], "max": 25, "desc": "自动评分：盈利能力+现金流"},
        {"label": "估值吸引力", "pts": scores["valuation"], "max": 25, "desc": "自动评分：估值区间参考"},
        {"label": "资本结构", "pts": scores["capital"], "max": 25, "desc": "自动评分：基石+新旧股比例"},
    ]

    # 风险条目
    risks = []
    if gms and gms[-1] < 30:
        risks.append("<strong>毛利率偏低</strong>：低毛利率业务抗风险能力较弱")
    if len(revenues) >= 2 and revenues[-2] > 0:
        g = (revenues[-1] - revenues[-2]) / revenues[-2] * 100
        if g < 10:
            risks.append("<strong>收入增速放缓</strong>：增长动力不足，需关注")
    if not risks:
        risks.append("<strong>信息待补充</strong>：建议手动补充招股书详细分析")

    list_date = fin.get("listDate", "")

    return {
        "code": code,
        "name": name,
        "nameEn": "",
        "sector": "待补充",
        "listDate": list_date,
        "subDate": "—",
        "price": price if price else None,
        "lotSize": lot,
        "entryFee": entry_fee,
        "totalIssue": "—",
        "publicIssue": "—",
        "greenshoe": "—",
        "mktCapH": "—",
        "pe": "—",
        "verdict": verdict,
        "verdictLabel": verdict_label,
        "score": total,
        "status": "hot",
        "statusText": "新上市",
        "conclusion": conclusion,
        "scoreBreakdown": score_breakdown,
        "financials": [
            {"label": "收入", "y2023": "—", "y2024": amt(revenues[-2]) if len(revenues) >= 2 else "—", "y2025": amt(revenues[-1]) if revenues else "—"},
            {"label": "毛利率", "y2023": "—", "y2024": pct(gms[-2]) if len(gms) >= 2 else "—", "y2025": pct(gms[-1]) if gms else "—"},
            {"label": "净利润", "y2023": "—", "y2024": amt(nps[-2]) if len(nps) >= 2 else "—", "y2025": amt(nps[-1]) if nps else "—"},
        ],
        "risks": risks,
        "actions": [
            {"date": "上市后", "title": "观察价格", "desc": "关注上市后估值收敛情况"},
            {"date": "中期", "title": "盈利兑现", "desc": "跟踪全年净利润是否实质性增长"},
        ],
        "subscription": None,
        "_auto": True,
        "_updatedAt": datetime.date.today().isoformat(),
    }


# ─────────────────────────────────────────────
# 7. 飞书通知
# ─────────────────────────────────────────────
def send_feishu(new_stocks, webhook_url):
    if not webhook_url or not new_stocks:
        return
    lines = [f"📊 港股IPO仪表盘更新 [{datetime.date.today()}]", ""]
    lines.append(f"新增 {len(new_stocks)} 只新股：")
    for s in new_stocks:
        emoji = "✅" if s["verdict"] == "da" else ("👀" if s["verdict"] == "watch" else "❌")
        price_str = f"HK${s['price']}" if s.get("price") else "转板"
        lines.append(
            f"{emoji} {s['code']} {s['name']}  "
            f"| {price_str} | 评分{s['score']}/100 | {s['verdictLabel']}"
        )
    lines.append("")
    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")

    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"[OK] Feishu notified: {resp.status_code}")
    except Exception as e:
        print(f"[WARN] Feishu notification failed: {e}")


# ─────────────────────────────────────────────
# 8. 主流程
# ─────────────────────────────────────────────
def main():
    print(f"[{datetime.datetime.now()}] 开始抓取港交所新股...")

    # 加载现有 data.json
    data_path = os.path.abspath(DATA_JSON_PATH)
    with open(data_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_codes = {s["code"] for s in existing}
    print(f"现有数据：{len(existing)} 只股票，代码：{sorted(existing_codes)}")

    # 抓取港交所列表
    listings = fetch_hkex_listings()
    print(f"港交所返回：{len(listings)} 条记录")

    new_stocks = []
    for listing in listings:
        code = listing["code"]
        if code in existing_codes:
            print(f"  跳过 {code}（已存在）")
            continue

        print(f"  分析新股 {code} {listing['name']}...")
        fin = {}

        # 尝试下载并解析招股书
        if pdfplumber:
            pdf_url = search_prospectus_url(code)
            if pdf_url:
                print(f"    下载招股书: {pdf_url}")
                pdf_bytes = download_pdf(pdf_url)
                if pdf_bytes:
                    fin = extract_financials(pdf_bytes)
                    print(f"    提取到: price={fin.get('price')}, gm={fin.get('grossMargins')}")
            else:
                print(f"    未找到招股书PDF")
        else:
            print("    pdfplumber 未安装，跳过PDF分析")

        # 合并列表中的日期
        if listing.get("listDate") and not fin.get("listDate"):
            fin["listDate"] = listing["listDate"]

        scores, total, verdict, label = calculate_score(fin)
        entry = build_stock_entry(code, listing["name"] or code, fin, scores, total, verdict, label)
        new_stocks.append(entry)
        print(f"    → 评分 {total}/100，{label}")
        time.sleep(1)  # 礼貌性延迟

    if new_stocks:
        # 合并并按上市日期倒序排列
        all_stocks = new_stocks + existing
        all_stocks.sort(
            key=lambda s: s.get("listDate") or "0000-00-00",
            reverse=True,
        )
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(all_stocks, f, ensure_ascii=False, indent=2)
        print(f"[OK] data.json 已更新，共 {len(all_stocks)} 只股票")

        # 将新股信息写入临时文件，供 workflow 在部署完成后发通知
        # 避免在 Pages 部署前就推送飞书通知（用户点击链接时页面尚未更新）
        notify_path = os.path.join(os.path.dirname(__file__), "..", ".notify_pending.json")
        with open(os.path.abspath(notify_path), "w", encoding="utf-8") as f:
            json.dump(new_stocks, f, ensure_ascii=False, indent=2)
        print(f"[OK] 通知队列已写入 .notify_pending.json，等待 Pages 部署后发送")
    else:
        print("[OK] 无新增股票，data.json 未变动")


if __name__ == "__main__":
    main()
