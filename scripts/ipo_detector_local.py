#!/usr/bin/env python3
"""
本地IPO新股检测脚本（使用Futu OpenAPI）
--------------------------------------------
每天 09:05 / 13:05 HKT 由 launchd 运行。
检测 Futu 返回的港股IPO列表，对尚未录入 index.html 的新股：
  1. 生成 v2.0 五维格式 AUTO-STUB（JS格式，可直接渲染）
  2. 插入 index.html stocks[] 头部
  3. git commit + push（触发 GitHub Pages 自动部署）
  4. 飞书通知，提醒人工补充完整分析

依赖：
  pip install futu-api requests
  本机需运行 FutuOpenD（默认 127.0.0.1:11111）

注意：
  - listTs 从 Futu 返回的上市日期计算 BJ 00:00，已规避 09:30 偏移问题
  - applyEndTs 由 Futu apply_end_time 提供，通常准确（BJ 10:00 截止）
  - 两个时间戳在 stub 中均标注"待招股书核实"，人工补全时须核对
"""

import os
import re
import sys
import time
import datetime
import subprocess
import requests

BJ_OFFSET = datetime.timezone(datetime.timedelta(hours=8))

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/bfb0bc75-5d4f-4c88-b587-1f65ef62abbc",
)
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# 只关注距今 N 天内上市的股票（过滤过旧历史数据）
FUTURE_DAYS_WINDOW = 60   # 未来60天内上市
PAST_DAYS_WINDOW   = 30   # 过去30天内上市（已上市但可能漏录）


# ─────────────────────────────────────────────
# 1. 读取 index.html 中已有股票代码
# ─────────────────────────────────────────────
def get_existing_codes(html_path):
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    codes = set()
    for m in re.finditer(r"code:\s*'(\d{5})'", content):
        codes.add(m.group(1))
    for m in re.finditer(r'"code":\s*"(\d{5})"', content):
        codes.add(m.group(1))
    return codes, content


# ─────────────────────────────────────────────
# 2. 通过 Futu API 获取港股IPO列表
# ─────────────────────────────────────────────
def fetch_ipo_list_from_futu():
    try:
        import futu as ft
    except ImportError:
        print("[ERROR] futu-api 未安装，请运行: pip install futu-api")
        sys.exit(1)

    ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, data = ctx.get_ipo_list(market="HK")
        if ret != 0:
            print(f"[ERROR] get_ipo_list 失败: {data}")
            return []
        records = data.to_dict("records")
        print(f"[OK] Futu 返回 {len(records)} 条港股IPO记录")
        return records
    finally:
        ctx.close()


# ─────────────────────────────────────────────
# 3. 解析单条 IPO 记录
# ─────────────────────────────────────────────
def parse_ipo_record(record):
    """
    从 Futu get_ipo_list 返回的记录中提取关键字段。
    字段名可能因 SDK 版本不同略有差异，优先取常见命名。
    """
    # ── 代码
    code_raw = str(record.get("code", "") or "")
    code = code_raw.replace("HK.", "").zfill(5)

    # ── 名称
    name = str(record.get("name", "") or code)

    # ── 上市日期（list_time 是日期字符串如 '2026-05-20'，从此计算 listTs）
    list_date_str = str(record.get("list_time", "") or "")
    list_ts = 0
    if list_date_str:
        try:
            dt = datetime.datetime.strptime(list_date_str, "%Y-%m-%d")
            # listTs = 上市日 BJ 00:00（按 CLAUDE.md 公式，不直接用 Futu list_timestamp）
            list_ts = int(datetime.datetime(
                dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=BJ_OFFSET
            ).timestamp())
        except ValueError:
            pass

    # ── 申购截止（apply_end_timestamp 已验证为 BJ 10:00 的 Unix 秒，可直接用）
    apply_end_ts_raw = record.get("apply_end_timestamp")
    apply_end_ts = 0
    apply_end_date_str = str(record.get("apply_end_time", "") or "")
    if apply_end_ts_raw and str(apply_end_ts_raw) not in ("N/A", "nan", ""):
        try:
            apply_end_ts = int(float(apply_end_ts_raw))
        except (ValueError, TypeError):
            pass

    # ── 发行价（优先取 list_price，其次 ipo_price_max）
    def _float(k):
        v = record.get(k)
        if v is None or str(v) in ("N/A", "nan", ""):
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    price = _float("list_price") or _float("ipo_price_max") or _float("ipo_price_min")
    lot_size = int(_float("lot_size") or 100)
    # entrance_price = Futu 已计算好的 1 手入场费（含费用），优先使用
    entry_fee = _float("entrance_price") or (round(price * lot_size * 1.01005, 2) if price else 0)

    # 暗盘日：上市日前一天（自然日，可为周末；周一上市→周日暗盘也正常）
    grey_date = ""
    if list_date_str:
        try:
            ld = datetime.datetime.strptime(list_date_str, "%Y-%m-%d")
            grey_date = (ld - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return {
        "code": code,
        "name": name,
        "list_date": list_date_str,
        "list_ts": list_ts,
        "apply_end_ts": apply_end_ts,
        "apply_end_date": apply_end_date_str,
        "price": price,
        "lot_size": lot_size,
        "entry_fee": entry_fee,
        "grey_date": grey_date,
    }


# ─────────────────────────────────────────────
# 4. 过滤：只处理时间窗口内的股票
# ─────────────────────────────────────────────
def is_in_window(parsed):
    list_date = parsed.get("list_date")
    if not list_date:
        return False  # 无上市日期，跳过
    try:
        ld = datetime.datetime.strptime(list_date, "%Y-%m-%d").date()
        today = datetime.datetime.now(tz=BJ_OFFSET).date()
        return (today - datetime.timedelta(days=PAST_DAYS_WINDOW)) <= ld <= (
            today + datetime.timedelta(days=FUTURE_DAYS_WINDOW)
        )
    except ValueError:
        return False


# ─────────────────────────────────────────────
# 5. 构建 v2.0 五维格式的 JS stub 字符串
# ─────────────────────────────────────────────
def build_js_stub(p):
    today = datetime.datetime.now(tz=BJ_OFFSET).strftime("%Y-%m-%d")
    code = p["code"]
    name = p["name"]
    list_ts = p["list_ts"]
    apply_end_ts = p["apply_end_ts"]
    list_date = p["list_date"]
    apply_end_date = p["apply_end_date"]
    price = p["price"]
    lot_size = p["lot_size"]
    entry_fee = p["entry_fee"]
    grey_date = p["grey_date"]

    price_str = f"{price}" if price else "null"
    entry_fee_str = f"{entry_fee}" if entry_fee else "null"

    # applyEndTs 注释
    apply_comment = f"// 北京时间 {apply_end_date} 10:00（Futu提供，待招股书核实）" if apply_end_date else "// ⚠️ 待查招股书"
    # listTs 注释
    list_comment = f"// 北京时间 {list_date} 00:00（⚠️ 待招股书核实，禁用Futu原始时间戳）" if list_date else "// ⚠️ 待查招股书"

    stub = f"""  {{
    code: '{code}',
    applyEndTs: {apply_end_ts},   {apply_comment}
    listTs:     {list_ts},        {list_comment}
    name: '{name}',
    nameEn: '',
    sector: '⚠️ 待补充',
    listDate: '{list_date}',
    subDate: '⚠️ 待补充',
    price: {price_str},
    lotSize: {lot_size},
    entryFee: {entry_fee_str},
    totalIssue: '⚠️ 待补充',
    publicIssue: '⚠️ 待补充',
    greenshoe: '⚠️ 待查（需同时搜超額配股權和超額配售）',
    mktCapH: '⚠️ 待补充',
    pe: '⚠️ 待补充',
    verdict: 'wait', verdictLabel: '待分析', score: 0,
    position: '⚠️ 待分析',
    sponsors: '⚠️ 待补充',
    isTransfer: false,
    cornerstone: {{ total: '⚠️ 待补充', tier1: [], others: [] }},
    conclusion: '⚠️ AUTO-STUB [{today}] 由本地Futu API自动检测插入，请用Claude补充完整招股书分析。',
    scores: [
      {{ label: '业务质量',          pts: 0, max: 25, desc: '⚠️ 待分析' }},
      {{ label: '财务健康',          pts: 0, max: 25, desc: '⚠️ 待分析' }},
      {{ label: '估值吸引力',        pts: 0, max: 25, desc: '⚠️ 待分析' }},
      {{ label: '资本结构',          pts: 0, max: 25, desc: '⚠️ 待分析' }},
      {{ label: '市场叙事/赛道溢价', pts: 0, max: 15, desc: '⚠️ 待分析' }},
    ],
    financial: [
      {{ label: '收入（人民币亿元）', y2023: '—', y2024: '—', y2025: '—' }},
      {{ label: '毛利率',             y2023: '—', y2024: '—', y2025: '—' }},
      {{ label: '净利润（人民币M）',  y2023: '—', y2024: '—', y2025: '—' }},
      {{ label: '收入增速',           y2023: '—', y2024: '—', y2025: '—' }},
    ],
    cfChecks: [
      {{ icon: '⚠️', text: 'AUTO-STUB 自动插入，现金流数据待人工核实', tag: 'warn', tagText: '待分析' }},
    ],
    risks: ['<strong>待分析</strong>：请用Claude补充完整招股书分析'],
    actions: [
      {{ date: '⚠️ 待填', title: '申购截止', desc: '请补充具体时间节点' }},
    ],
    subscription: {{
      scenarios: [
        {{ label: '保守', mult: 0, premPct: 0 }},
        {{ label: '基准', mult: 0, premPct: 0 }},
        {{ label: '乐观', mult: 0, premPct: 0 }},
      ],
      recClass: 'wait', recTitle: '⚠️ 待分析',
      lots: 0, method: '待定', marginOk: false,
      marginTip: '⚠️ 待分析',
      rationale: 'AUTO-STUB，待人工补充分析。',
      urgentTip: '⚠️ 请补充截止时间',
    }},
    greyMarket: {{
      date: '{grey_date}',
      price: null, changePct: null, peakPrice: null, peakChangePct: null
    }}
  }}"""
    return stub


# ─────────────────────────────────────────────
# 6. 将 stubs 插入 index.html
# ─────────────────────────────────────────────
def insert_stubs(html_content, stubs_to_insert):
    """在 const stocks = [ 后插入所有 stub 条目"""
    marker = "const stocks = ["
    pos = html_content.find(marker)
    if pos == -1:
        print("[ERROR] 未找到 'const stocks = ['，插入失败")
        return html_content, False

    insert_pos = pos + len(marker)
    today = datetime.datetime.now(tz=BJ_OFFSET).strftime("%Y-%m-%d")

    block = ""
    for code, name, stub_text in stubs_to_insert:
        block += (
            f"\n  // ⚠️ AUTO-STUB [{today}] Futu API自动检测，"
            f"请用Claude补充完整招股书分析后替换\n"
            f"{stub_text},"
        )

    return html_content[:insert_pos] + block + html_content[insert_pos:], True


# ─────────────────────────────────────────────
# 7. git commit + push
# ─────────────────────────────────────────────
def git_push(repo_dir, new_stocks_info):
    names = "、".join(f"{c}{n}" for c, n, _ in new_stocks_info)
    commit_msg = f"Auto: 新股检测 {names}（AUTO-STUB，待Claude分析）"
    try:
        subprocess.run(["git", "add", "index.html"], cwd=repo_dir, check=True)
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"], cwd=repo_dir
        )
        if result.returncode == 0:
            print("[OK] index.html 无变化（可能已存在），跳过 push")
            return False
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir, check=True)
        subprocess.run(["git", "push"], cwd=repo_dir, check=True)
        print(f"[OK] git push 完成：{commit_msg}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] git 操作失败: {e}")
        return False


# ─────────────────────────────────────────────
# 8. 飞书通知
# ─────────────────────────────────────────────
def send_feishu(new_stocks_info):
    today = datetime.datetime.now(tz=BJ_OFFSET).strftime("%Y-%m-%d")
    lines = [f"🔔 港股IPO仪表盘 发现新股 [{today}]", ""]
    lines.append(f"共 {len(new_stocks_info)} 只新股已自动插入仪表盘（AUTO-STUB）：")
    for code, name, list_date in new_stocks_info:
        lines.append(f"  📋 {code} {name}（预计上市：{list_date or '待定'}）")
    lines.append("")
    lines.append("👉 对话Claude：「帮我分析 XX 招股书，更新仪表盘」")
    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")

    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        result = resp.json()
        if result.get("code") == 0:
            print("[OK] 飞书通知已发送")
        else:
            print(f"[WARN] 飞书返回错误: {result}")
    except Exception as e:
        print(f"[WARN] 飞书通知失败: {e}")


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    now_bj = datetime.datetime.now(tz=BJ_OFFSET)
    print(f"[{now_bj.strftime('%Y-%m-%d %H:%M:%S')} BJ] 开始本地IPO新股检测（Futu API）...")

    html_path = os.path.abspath(INDEX_HTML_PATH)
    repo_dir = os.path.dirname(html_path)

    existing_codes, html_content = get_existing_codes(html_path)
    print(f"index.html 已有股票代码: {len(existing_codes)} 只")

    ipo_records = fetch_ipo_list_from_futu()
    if not ipo_records:
        print("[OK] Futu 未返回数据，退出")
        return

    stubs_to_insert = []
    new_stocks_info = []

    for record in ipo_records:
        parsed = parse_ipo_record(record)
        code = parsed["code"]

        if not code or code == "00000":
            continue
        if code in existing_codes:
            print(f"  跳过 {code}（已存在于 index.html）")
            continue
        if not is_in_window(parsed):
            print(f"  跳过 {code}（上市日 {parsed['list_date']} 超出时间窗口）")
            continue

        name = parsed["name"]
        list_date = parsed["list_date"]
        print(f"  发现新股: {code} {name}（上市: {list_date}）")

        stub_text = build_js_stub(parsed)
        stubs_to_insert.append((code, name, stub_text))
        new_stocks_info.append((code, name, list_date))

    if not stubs_to_insert:
        print("[OK] 无新增股票，退出")
        return

    # 插入 index.html
    new_content, ok = insert_stubs(html_content, stubs_to_insert)
    if not ok:
        return

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"[OK] 已将 {len(stubs_to_insert)} 只新股 stub 插入 index.html")

    # git push
    pushed = git_push(repo_dir, new_stocks_info)

    # 飞书通知（仅在成功 push 后发）
    if pushed:
        send_feishu(new_stocks_info)
    else:
        print("[INFO] 未推送，跳过飞书通知")


if __name__ == "__main__":
    main()
