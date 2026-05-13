#!/usr/bin/env python3
"""
grey_market_close.py — 暗盘收盘价自动写入
==========================================
设计原则：
  - 单次执行（18:40 HKT 由 launchd 触发，运行一次即退出）
  - 读写 index.html（不碰 data.json）
  - 无数据不写入，不覆盖已有数据
  - 自动 git commit + push
  - 发飞书收盘通知

依赖：futu-api, requests（pip install futu-api requests）
前提：FutuOpenD 运行在 127.0.0.1:11111
"""

import re
import os
import sys
import subprocess
import datetime
import requests

try:
    from futu import OpenQuoteContext, RET_OK
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "futu-api", "-q"], check=False)
    from futu import OpenQuoteContext, RET_OK

# ── 路径配置 ──────────────────────────────────────────────────
REPO_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML     = os.path.join(REPO_DIR, "index.html")
LOG_FILE       = os.path.join(REPO_DIR, "logs", "grey_market_close.log")
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/bfb0bc75-5d4f-4c88-b587-1f65ef62abbc"
)
FUTU_HOST, FUTU_PORT = "127.0.0.1", 11111


# ── 工具函数 ──────────────────────────────────────────────────
def log(msg):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_today_bj():
    """返回北京时间今日日期字符串 YYYY-MM-DD"""
    bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    return bj.strftime("%Y-%m-%d")


# ── 解析 index.html ───────────────────────────────────────────
def find_stocks_needing_update(content, today_str):
    """
    从 index.html 找出 greyMarket.date = today AND price = null 的股票。
    返回列表：[{ code, name, ipo_price, gm_original_str }, ...]
    """
    # greyMarket 行格式固定为一行：
    #   greyMarket: { date: 'YYYY-MM-DD', price: null, changePct: null, peakPrice: null, peakChangePct: null }
    gm_null_pattern = re.compile(
        r"greyMarket:\s*\{\s*date:\s*'" + re.escape(today_str) +
        r"',\s*price:\s*null,\s*changePct:\s*null,\s*peakPrice:\s*null,\s*peakChangePct:\s*null\s*\}"
    )

    stocks = []
    for m in gm_null_pattern.finditer(content):
        preceding = content[:m.start()]

        # 向前找最近的 code: 'XXXXX'
        code_matches = list(re.finditer(r"code:\s*'(\d{5})'", preceding))
        if not code_matches:
            continue
        code = code_matches[-1].group(1)

        # 向前找最近的 name: '...'
        name_matches = list(re.finditer(r"name:\s*'([^']+)'", preceding))
        name = name_matches[-1].group(1) if name_matches else code

        # 从 code 位置到 greyMarket 之间找 price: XX.XX（IPO 发行价）
        seg_start = code_matches[-1].start()
        segment   = content[seg_start:m.start()]
        price_m   = re.search(r"^\s+price:\s*([\d.]+),", segment, re.MULTILINE)
        if not price_m:
            log(f"[WARN] {code}: 找不到 IPO 价格字段，跳过")
            continue
        ipo_price = float(price_m.group(1))

        stocks.append({
            "code":          code,
            "name":          name,
            "ipo_price":     ipo_price,
            "gm_original":   m.group(0),   # 用于精确替换
        })
        log(f"[发现] {code} {name}  IPO价 HK${ipo_price}")

    return stocks


# ── Futu 行情 ─────────────────────────────────────────────────
def fetch_last_prices(codes):
    """
    通过 Futu OpenAPI 拉取快照。
    返回 { '07666': 29.4, ... }
    """
    hk_codes = [f"HK.{c}" for c in codes]
    prices   = {}
    try:
        ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        ret, data = ctx.get_market_snapshot(hk_codes)
        ctx.close()
        if ret != RET_OK:
            log(f"[ERROR] Futu snapshot 失败: {data}")
            return prices
        for _, row in data.iterrows():
            code      = row["code"].replace("HK.", "").zfill(5)
            last_p    = float(row.get("last_price", 0) or 0)
            prev_c    = float(row.get("prev_close",  0) or 0)
            prices[code] = {"last_price": last_p, "prev_close": prev_c}
            log(f"  Futu → {code}: last={last_p}  prev_close={prev_c}")
    except Exception as e:
        log(f"[ERROR] Futu 连接异常: {e}")
    return prices


# ── 写入 index.html ───────────────────────────────────────────
def apply_updates(content, today_str, updates):
    """
    updates: [{ gm_original, last_price, change_pct }, ...]
    返回更新后的 content
    """
    for u in updates:
        new_str = (
            f"greyMarket: {{ date: '{today_str}', "
            f"price: {u['last_price']}, "
            f"changePct: {u['change_pct']}, "
            f"peakPrice: null, peakChangePct: null }}"
        )
        if u["gm_original"] not in content:
            log(f"[WARN] {u['code']}: 原始字符串已改变，跳过替换")
            continue
        content = content.replace(u["gm_original"], new_str, 1)
    return content


# ── Git ───────────────────────────────────────────────────────
def git_commit_push(codes_updated):
    msg = f"自动写入暗盘收盘价: {', '.join(codes_updated)}"
    try:
        subprocess.run(["git", "-C", REPO_DIR, "add", "index.html"], check=True)
        subprocess.run(["git", "-C", REPO_DIR, "commit", "-m", msg], check=True)
        subprocess.run(["git", "-C", REPO_DIR, "push"], check=True)
        log(f"[OK] git push 完成: {msg}")
        return True
    except subprocess.CalledProcessError as e:
        log(f"[ERROR] git 操作失败: {e}")
        return False


# ── 飞书通知 ──────────────────────────────────────────────────
def send_feishu(results, today_str):
    lines = [f"📊 IPO仪表盘 暗盘收盘自动报告 [{today_str} 18:30 HKT]", ""]
    for r in results:
        sign  = "+" if r["change_pct"] >= 0 else ""
        emoji = "🚀" if r["change_pct"] >= 20 else ("✅" if r["change_pct"] > 0 else "🔴")
        lines.append(f"{emoji} {r['code']} {r['name']}")
        lines.append(f"   IPO价 HK${r['ipo_price']}  →  收盘 HK${r['last_price']}  ({sign}{r['change_pct']:.1f}%)")
        lines.append("")
    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")
    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        code = resp.json().get("code")
        log(f"[OK] 飞书通知已发送" if code == 0 else f"[WARN] 飞书错误: {resp.json()}")
    except Exception as e:
        log(f"[WARN] 飞书通知失败: {e}")


# ── 主流程 ────────────────────────────────────────────────────
def main():
    today_str = get_today_bj()
    log(f"=== grey_market_close 启动 [{today_str}] ===")

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        content = f.read()

    stocks = find_stocks_needing_update(content, today_str)
    if not stocks:
        log("今日无需更新的暗盘股票，退出")
        return

    prices = fetch_last_prices([s["code"] for s in stocks])
    if not prices:
        log("[ERROR] 无法从 Futu 获取价格（OpenD 是否运行？），退出")
        sys.exit(1)

    updates  = []
    results  = []

    for s in stocks:
        code  = s["code"]
        p     = prices.get(code)
        if not p or p["last_price"] <= 0:
            log(f"[WARN] {code}: 价格无效（{p}），跳过")
            continue

        last_price = round(p["last_price"], 2)
        ipo_price  = s["ipo_price"]

        # 使用 Futu prev_close 作为参考（与 IPO 价格核对，差距>10%则用 ipo_price）
        prev_close = p["prev_close"]
        ref_price  = ipo_price
        if prev_close > 0 and abs(prev_close - ipo_price) / ipo_price > 0.10:
            log(f"[WARN] {code}: prev_close={prev_close} 与 ipo_price={ipo_price} 差距>10%，以 ipo_price 为参考")
        elif prev_close > 0:
            ref_price = prev_close   # prev_close 更准确（反映最终定价）

        change_pct = round((last_price - ref_price) / ref_price * 100, 1)
        log(f"[计算] {code}: last={last_price}, ref={ref_price}, changePct={change_pct:+.1f}%")

        updates.append({
            "code":        code,
            "gm_original": s["gm_original"],
            "last_price":  last_price,
            "change_pct":  change_pct,
        })
        results.append({
            "code":       code,
            "name":       s["name"],
            "ipo_price":  ipo_price,
            "last_price": last_price,
            "change_pct": change_pct,
        })

    if not updates:
        log("所有股票价格无效，不写入，退出")
        return

    content = apply_updates(content, today_str, updates)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"[OK] index.html 已更新 {len(updates)} 只股票")

    pushed = git_commit_push([u["code"] for u in updates])
    send_feishu(results, today_str)

    log(f"=== 完成，{'git push 成功' if pushed else 'git push 失败，请手动检查'} ===")


if __name__ == "__main__":
    main()
