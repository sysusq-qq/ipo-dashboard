#!/usr/bin/env python3
"""
申购截止提醒脚本
每天 09:40 HKT 运行（富途 10:00 截止，提前20分钟提醒）
若当天有股票截止申购，发飞书通知
"""

import os
import re
import time
import datetime
import requests

FEISHU_WEBHOOK  = os.environ.get("FEISHU_WEBHOOK", "")
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

BJ_OFFSET = datetime.timezone(datetime.timedelta(hours=8))


def get_bj_today():
    return datetime.datetime.now(tz=BJ_OFFSET).date()


def apply_end_date(s):
    """
    返回该股票的申购截止日期（datetime.date），无法确定则返回 None。
    优先使用 applyEndTs（标准 UTC Unix 秒），其次解析 subDate 字符串。
    """
    ts = s.get("applyEndTs")
    if ts:
        try:
            return datetime.datetime.fromtimestamp(ts, tz=BJ_OFFSET).date()
        except Exception:
            pass

    sub = s.get("subDate", "")
    if sub and "不适用" not in sub and "—" not in sub:
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", sub.strip())
        if m:
            try:
                return datetime.date.fromisoformat(m.group(1))
            except ValueError:
                pass
    return None


def is_already_done(s, now_ts):
    """
    已上市或转板的股票不发提醒。
    用 listTs / isTransfer 判断，不依赖已废弃的 status 字段。
    """
    if s.get("isTransfer"):
        return True
    list_ts = s.get("listTs")
    if list_ts and now_ts >= list_ts:
        return True
    return False


def send_feishu_reminder(stocks_due, webhook_url):
    if not webhook_url or not stocks_due:
        return
    today = get_bj_today()
    lines = [f"⏰ IPO仪表盘 申购截止提醒 [{today}]", ""]
    for s in stocks_due:
        price_str = f"HK${s['price']}" if s.get("price") else "—"
        entry_str = f"HK${s['entryFee']:,.0f}" if s.get("entryFee") else "—"
        emoji = "✅" if s["verdict"] == "da" else ("👀" if s["verdict"] == "watch" else "❌")
        lines.append(f"{emoji} {s['code']} {s['name']}")
        lines.append(f"   发行价：{price_str}  每手入场费：{entry_str}")
        lines.append(f"   评分：{s['score']}/115 | 建议：{s['verdictLabel']}")
        lines.append(f"   ⚠️ 富途今日 10:00 截止，还有约20分钟！")
        lines.append("")
    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")

    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    for attempt in range(1, 4):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                print(f"[OK] 提醒已发送，涉及 {len(stocks_due)} 只股票")
                return
            else:
                print(f"[WARN] 飞书返回错误（第{attempt}次）: {result}")
                if attempt < 3:
                    time.sleep(30)
        except Exception as e:
            print(f"[WARN] 飞书通知失败（第{attempt}次）: {e}")
            if attempt < 3:
                time.sleep(20)
    print(f"[ERROR] 飞书通知最终失败，已重试3次")


def parse_stocks_from_html(html_path):
    """从 index.html stocks 数组解析股票列表，返回 dict 列表。"""
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    stocks = []
    for block in re.split(r"(?=\n\s*\{\s*\n\s*code:)", content):
        m = re.search(r"code:\s*'(\d{5})'", block)
        if not m:
            continue
        s = {"code": m.group(1)}

        m2 = re.search(r"name:\s*'([^']+)'", block)
        s["name"] = m2.group(1) if m2 else s["code"]

        m2 = re.search(r"applyEndTs:\s*(\d+)", block)
        s["applyEndTs"] = int(m2.group(1)) if m2 else None

        m2 = re.search(r"listTs:\s*(\d+)", block)
        s["listTs"] = int(m2.group(1)) if m2 else None

        m2 = re.search(r"isTransfer:\s*(true|false)", block)
        s["isTransfer"] = (m2.group(1) == "true") if m2 else False

        m2 = re.search(r"subDate:\s*'([^']+)'", block)
        s["subDate"] = m2.group(1) if m2 else ""

        m2 = re.search(r"^\s+price:\s*([\d.]+),", block, re.MULTILINE)
        s["price"] = float(m2.group(1)) if m2 else None

        m2 = re.search(r"entryFee:\s*([\d.]+)", block)
        s["entryFee"] = float(m2.group(1)) if m2 else None

        m2 = re.search(r"verdict:\s*'([^']+)'", block)
        s["verdict"] = m2.group(1) if m2 else "wait"

        m2 = re.search(r"verdictLabel:\s*'([^']+)'", block)
        s["verdictLabel"] = m2.group(1) if m2 else "—"

        m2 = re.search(r"score:\s*(\d+)", block)
        s["score"] = int(m2.group(1)) if m2 else 0

        stocks.append(s)
    return stocks


def main():
    today = get_bj_today()
    now_ts = datetime.datetime.now(tz=BJ_OFFSET).timestamp()
    print(f"[{datetime.datetime.now(tz=BJ_OFFSET).strftime('%Y-%m-%d %H:%M:%S')} BJ] "
          f"检查今日申购截止股票（{today}）...")

    html_path = os.path.abspath(INDEX_HTML_PATH)
    stocks = parse_stocks_from_html(html_path)

    stocks_due = []
    for s in stocks:
        if is_already_done(s, now_ts):
            continue
        end_date = apply_end_date(s)
        if end_date == today:
            stocks_due.append(s)
            print(f"  → {s['code']} {s['name']} 今日截止")

    if stocks_due:
        webhook = FEISHU_WEBHOOK or "https://open.feishu.cn/open-apis/bot/v2/hook/bfb0bc75-5d4f-4c88-b587-1f65ef62abbc"
        send_feishu_reminder(stocks_due, webhook)
    else:
        print("[OK] 今日无股票申购截止，不发提醒")


if __name__ == "__main__":
    main()
