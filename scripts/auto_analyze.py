#!/usr/bin/env python3
"""
自动招股书分析模块
------------------
供 ipo_detector_local.py 调用。
流程：搜索招股书PDF → 提取关键章节 → Claude API生成完整JS条目

输出：符合 index.html stocks[] 格式的完整 JavaScript 字符串
"""

import io
import os
import re
import time
import datetime
import requests

BJ_OFFSET = datetime.timezone(datetime.timedelta(hours=8))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}

# Claude API 调用的模型
CLAUDE_MODEL = "claude-sonnet-4-6"

# ─────────────────────────────────────────────
# 1. 搜索招股书 PDF 链接
#    从 HKEX 主板新上市信息页提取（该页面有直链，不需要 AJAX）
# ─────────────────────────────────────────────
HKEX_MAIN_BOARD_URL = (
    "https://www2.hkexnews.hk/New-Listings/New-Listing-Information/Main-Board?sc_lang=zh-HK"
)

# 简单缓存：同一进程只抓一次
_hkex_page_cache = {"content": None}


def _get_hkex_listing_page():
    if _hkex_page_cache["content"] is None:
        try:
            resp = requests.get(HKEX_MAIN_BOARD_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            _hkex_page_cache["content"] = resp.text
        except Exception as e:
            print(f"    [WARN] 抓取 HKEX 主板上市页失败: {e}")
            _hkex_page_cache["content"] = ""
    return _hkex_page_cache["content"]


def search_prospectus_url(stock_code):
    """
    从 HKEX 主板新上市信息页找招股书 PDF。
    策略：解析 HTML 表格，找到股票代码所在的 <tr>，
    取该行所有 PDF 链接中最大的文件（招股书 >> 公告）。
    """
    from bs4 import BeautifulSoup

    code_bare = stock_code.lstrip("0") or stock_code  # "06872" → "6872"

    page = _get_hkex_listing_page()
    if not page:
        return None

    soup = BeautifulSoup(page, "lxml")
    target_row = None

    # 找包含该股票代码的 <tr>
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        first_cell = cells[0].get_text(strip=True)
        if first_cell in (stock_code, code_bare):
            target_row = tr
            break

    if target_row is None:
        print(f"    [WARN] HKEX 主板页未找到股票 {stock_code} 的行")
        return None

    # 提取该行所有 PDF 链接
    candidate_urls = []
    for a in target_row.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf") and "hkexnews.hk" in href:
            if not href.startswith("http"):
                href = "https://www1.hkexnews.hk" + href
            candidate_urls.append(href)

    if not candidate_urls:
        print(f"    [WARN] {stock_code} 的行中无 PDF 链接")
        return None

    print(f"    找到 {len(candidate_urls)} 个 PDF，检查文件大小...")

    best_url = None
    best_size = 0
    for url in candidate_urls:
        try:
            head = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            size = int(head.headers.get("Content-Length", 0))
            print(f"      {url[-60:]} — {size/1024/1024:.1f}MB")
            if size > best_size:
                best_size = size
                best_url = url
        except Exception:
            pass

    if best_url and best_size > 500_000:
        print(f"    [OK] 选定: {best_url[-70:]} ({best_size/1024/1024:.1f}MB)")
        return best_url

    print(f"    [WARN] 未找到足够大的招股书（最大 {best_size/1024:.0f}KB）")
    return None


# ─────────────────────────────────────────────
# 2. 下载 PDF（流式，限制大小）
# ─────────────────────────────────────────────
def download_pdf(url, max_mb=40):
    try:
        resp = requests.get(url, headers=HEADERS, stream=True, timeout=90)
        resp.raise_for_status()
        chunks = []
        total = 0
        limit = max_mb * 1024 * 1024
        for chunk in resp.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                print(f"    [INFO] PDF 已达 {max_mb}MB 上限，截断下载")
                break
        data = b"".join(chunks)
        print(f"    [OK] 下载完成：{len(data)/1024/1024:.1f} MB")
        return data
    except Exception as e:
        print(f"    [WARN] download_pdf: {e}")
    return None


# ─────────────────────────────────────────────
# 3. 从 PDF 提取关键文本
# ─────────────────────────────────────────────
def extract_key_sections(pdf_bytes, max_chars=80000):
    """
    提取招股书关键章节文本：
    - 前50页（封面、摘要、发售结构、财务摘要）
    - 基石投资者相关页面
    - 绿鞋（超額配股權）相关页面
    返回合并后的文本字符串（限 max_chars 字符）
    """
    try:
        import pdfplumber
    except ImportError:
        print("    [WARN] pdfplumber 未安装")
        return ""

    try:
        sections = []
        kw_pages = []  # 含关键词的页面

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            print(f"    [INFO] PDF 共 {total_pages} 页")

            # 前50页（包含摘要/财务/发售结构）
            front_pages = min(50, total_pages)
            for i in range(front_pages):
                text = pdf.pages[i].extract_text() or ""
                sections.append(f"=== 第{i+1}页 ===\n{text}")

            # 搜索关键词页面（基石、绿鞋、财务）
            keywords = ["基石投資者", "基石投资者", "超額配股權", "超額配售",
                        "財務資料", "财务资料", "財務摘要", "财务摘要",
                        "毛利率", "保荐人", "保薦人"]
            for i in range(front_pages, total_pages):
                text = pdf.pages[i].extract_text() or ""
                if any(kw in text for kw in keywords):
                    kw_pages.append(f"=== 第{i+1}页（关键词页）===\n{text}")
                    if len(kw_pages) >= 30:  # 最多额外取30页
                        break

        all_text = "\n".join(sections + kw_pages)
        if len(all_text) > max_chars:
            all_text = all_text[:max_chars] + "\n...[文本已截断]"
        print(f"    [OK] 提取文本 {len(all_text)} 字符")
        return all_text

    except Exception as e:
        print(f"    [WARN] extract_key_sections: {e}")
        return ""


# ─────────────────────────────────────────────
# 4. 构建 Claude 分析 Prompt
# ─────────────────────────────────────────────
SCORING_RULES = """
## 五维评分规则（满分 115 分）

| 维度 | 满分 | 核心看点 |
|------|------|---------|
| 业务质量 | 25 | 护城河/增速/市占率/全球化/技术稀缺性 |
| 财务健康 | 25 | 净利润趋势/毛利率/净利率/现金流 |
| 估值吸引力 | 25 | PE/PS/同类对标 |
| 资本结构 | 25 | 基石质量与独立性/绿鞋/保荐人背书 |
| 市场叙事/赛道溢价 | 15 | 当前市场热度 |

市场叙事评分标准：
- 12-15分：当前最热赛道（AI芯片/机器人/低空经济）+ 港股稀缺
- 8-11分：热门赛道但同类已有多只
- 4-7分：赛道中性
- 0-3分：冷门赛道

## 结论标准
- 五维合计 ≥70：verdict='da', verdictLabel='打'
- 55-69：verdict='wait', verdictLabel='轻仓'（1手现金仓）
- 40-54：verdict='wait', verdictLabel='观望'
- <40：verdict='no', verdictLabel='不打'

## 硬性触发规则（优先级高于评分）
1. A+H股 H股折价>30% → 直接打
2. 18C + 顶级机构基石占比>30% → 最低1手现金仓彩票
3. 认购倍数>200x（上市前确认）→ 升级积极打

## 绿鞋核查：必须同时搜索「超額配股權」和「超額配售」两个词，两者均无才算无绿鞋
"""


def build_prompt(parsed, prospectus_text):
    code = parsed["code"]
    name = parsed["name"]
    list_date = parsed["list_date"]
    apply_end_date = parsed["apply_end_date"]
    apply_end_ts = parsed["apply_end_ts"]
    list_ts = parsed["list_ts"]
    grey_date = parsed["grey_date"]
    price = parsed["price"]
    lot_size = parsed["lot_size"]
    entry_fee = parsed["entry_fee"]

    # 计算 subDate（需要从招股书提取，此处给Claude占位）
    list_dt = datetime.datetime.strptime(list_date, "%Y-%m-%d") if list_date else None

    prompt = f"""你是专业的港股IPO分析师。请基于以下招股书文本，对 {code} {name} 进行完整的 v2.0 五维打分分析，并输出符合格式的 JavaScript 对象。

## 已确认的基础数据（勿修改）
- 股票代码：{code}
- 公司名称：{name}
- 上市日期：{list_date}
- 申购截止日：{apply_end_date}（BJ 10:00）
- 发行价：HK${price}
- 每手股数：{lot_size}
- 1手入场费：HK${entry_fee}（含佣金）
- applyEndTs：{apply_end_ts}（已验证，直接使用）
- listTs：{list_ts}（已按BJ 00:00计算，直接使用）
- 暗盘日期：{grey_date}

{SCORING_RULES}

## 输出要求
1. 只输出一个完整的 JavaScript 对象，从 `{{` 开始，到 `}}` 结束
2. 不要任何解释文字、代码块标记（不要```javascript）
3. 字符串用单引号，键名不加引号（JS格式）
4. applyEndTs / listTs / greyMarket.date 使用上面提供的确认值
5. score = 五维分数之和（最大115）
6. conclusion 不超过200字，含核心逻辑和明确结论
7. 如招股书未提及某字段，填 '待确认' 或合理推断

## JavaScript 格式模板（严格按此结构输出）
{{
  code: '{code}',
  applyEndTs: {apply_end_ts},
  listTs:     {list_ts},
  name: '{name}',
  nameEn: '从招股书提取英文名',
  sector: '行业 / 子赛道 / 特殊标签',
  listDate: '{list_date}',
  subDate: '从招股书提取 YYYY-MM-DD ~ YYYY-MM-DD',
  price: {price},
  lotSize: {lot_size},
  entryFee: {entry_fee},
  totalIssue: 'X万股H股（全球发售）',
  publicIssue: 'X万股（10%香港公开发售）',
  greenshoe: '有（最多X万股，约15%） 或 无',
  mktCapH: '约HK$XX亿（总股本X亿股×HK${price}）',
  pe: '约XXx（2025A）/ PS Xx',
  verdict: 'da 或 wait 或 no',
  verdictLabel: '打 或 轻仓 或 观望 或 不打',
  score: 0,
  position: 'X手现金仓 或 孖展X手 + 现金X手',
  sponsors: '保荐人名称',
  isTransfer: false,
  cornerstone: {{
    total: 'USD XX万（≈HK$XX亿），占发售股份XX%',
    tier1: [
      {{ name: '机构名称', amt: 'USD XXX万', lockup: 'X个月' }},
    ],
    others: []
  }},
  conclusion: '200字以内核心逻辑和结论',
  scores: [
    {{ label: '业务质量',          pts: 0, max: 25, desc: '简要说明得分依据' }},
    {{ label: '财务健康',          pts: 0, max: 25, desc: '简要说明得分依据' }},
    {{ label: '估值吸引力',        pts: 0, max: 25, desc: '简要说明得分依据' }},
    {{ label: '资本结构',          pts: 0, max: 25, desc: '简要说明得分依据' }},
    {{ label: '市场叙事/赛道溢价', pts: 0, max: 15, desc: '简要说明得分依据' }},
  ],
  financial: [
    {{ label: '收入（人民币亿元）', y2023: '实际值', y2024: '实际值', y2025: '实际值' }},
    {{ label: '毛利率',             y2023: 'X.X%', y2024: 'X.X%', y2025: 'X.X%' }},
    {{ label: '净利润（人民币M）',  y2023: 'X', y2024: 'X', y2025: 'X' }},
    {{ label: '收入增速',           y2023: '—', y2024: '+X%', y2025: '+X%' }},
  ],
  cfChecks: [
    {{ icon: '✅ 或 ⚠️', text: '现金流关键描述', tag: 'ok 或 warn', tagText: '标签文字' }},
  ],
  risks: ['<strong>风险标题</strong>：风险说明'],
  actions: [
    {{ date: 'X月X日 节点名称', title: '操作标题', desc: '具体建议' }},
  ],
  subscription: {{
    scenarios: [
      {{ label: '保守', mult: 0,  premPct: 0 }},
      {{ label: '基准', mult: 0,  premPct: 0 }},
      {{ label: '乐观', mult: 0,  premPct: 0 }},
    ],
    recClass: 'da 或 wait 或 no',
    recTitle: '✅ 积极申购 或 ⚠️ 谨慎参与 或 ❌ 不建议参与',
    lots: 0,
    method: '现金仓 或 孖展X成',
    marginOk: true,
    marginTip: '孖展说明',
    rationale: '申购逻辑3-5句',
    urgentTip: '⏰ 截止{apply_end_date}(周?) 10:00；一人一手；...'
  }},
  greyMarket: {{
    date: '{grey_date}',
    price: null,
    changePct: null,
    peakPrice: null,
    peakChangePct: null
  }}
}}

## 招股书文本节选
{prospectus_text}
"""
    return prompt


# ─────────────────────────────────────────────
# 5. 调用 Claude API
# ─────────────────────────────────────────────
def call_claude(prompt):
    try:
        import anthropic

        # 支持内部 proxy 配置（ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN）
        api_key = (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        base_url = os.environ.get("ANTHROPIC_BASE_URL")

        kwargs = {"max_retries": 2}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        client = anthropic.Anthropic(**kwargs)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        print(f"    [ERROR] Claude API 调用失败: {e}")
        return None


# ─────────────────────────────────────────────
# 6. 从 Claude 响应中提取 JS 对象字符串
# ─────────────────────────────────────────────
def extract_js_object(raw_text):
    """
    从 Claude 响应中提取 { ... } JS 对象。
    Claude 有时会包裹在 ```javascript ... ``` 中，需要去掉。
    """
    if not raw_text:
        return None

    # 去掉代码块标记
    text = re.sub(r"```(?:javascript|js)?\s*", "", raw_text)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # 找到第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        print("    [WARN] 未找到有效的 JS 对象")
        return None

    return text[start : end + 1]


# ─────────────────────────────────────────────
# 7. 主入口：分析单只股票
# ─────────────────────────────────────────────
def analyze_stock(parsed):
    """
    对单只新股做完整 Claude API 分析。
    parsed: ipo_detector_local.py 中 parse_ipo_record() 的返回值
    返回 JS 对象字符串，失败返回 None
    """
    code = parsed["code"]
    name = parsed["name"]
    print(f"  [{code} {name}] 开始自动分析...")

    # 1. 搜索招股书
    pdf_url = search_prospectus_url(code)
    if not pdf_url:
        print(f"    [WARN] 未找到招股书PDF，跳过分析")
        return None
    print(f"    招股书 URL: {pdf_url}")

    # 2. 下载 PDF
    pdf_bytes = download_pdf(pdf_url)
    if not pdf_bytes:
        print(f"    [WARN] 下载失败，跳过分析")
        return None

    # 3. 提取文本
    prospectus_text = extract_key_sections(pdf_bytes)
    if not prospectus_text:
        print(f"    [WARN] 文本提取失败，跳过分析")
        return None

    # 4. 调用 Claude
    print(f"    调用 Claude API ({CLAUDE_MODEL})...")
    prompt = build_prompt(parsed, prospectus_text)
    raw = call_claude(prompt)
    if not raw:
        return None

    # 5. 提取 JS 对象
    js_obj = extract_js_object(raw)
    if not js_obj:
        print(f"    [WARN] Claude 响应解析失败")
        print(f"    原始响应前500字: {raw[:500]}")
        return None

    print(f"    [OK] 分析完成，JS 对象长度 {len(js_obj)} 字符")
    return js_obj
