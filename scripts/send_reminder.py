#!/usr/bin/env python3
"""
申购截止提醒脚本
在每只新股截止申购当天 11:40 HKT 运行
若当天有股票截止申购，发飞书通知
"""

import json
import os
import re
import datetime
import requests

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
DATA_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "data.json")


def parse_sub_end_date(sub_date_str):
    """
    从 subDate 字段提取截止日期
    支持格式：'2026-04-09 ~ 2026-04-14'
    """
    if not sub_date_str or "不适用" in sub_date_str or "—" in sub_date_str:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", sub_date_str.strip())
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def send_feishu_reminder(stocks_due, webhook_url):
    if not webhook_url or not stocks_due:
        return
    today = datetime.date.today()
    lines = [f"⏰ IPO仪表盘 申购截止提醒 [{today}]", ""]
    for s in stocks_due:
        price_str = f"HK${s['price']}" if s.get("price") else "转板"
        entry_str = f"HK${s['entryFee']:,.0f}" if s.get("entryFee") else "—"
        emoji = "✅" if s["verdict"] == "da" else ("👀" if s["verdict"] == "watch" else "❌")
        lines.append(f"{emoji} {s['code']} {s['name']}")
        lines.append(f"   发行价：{price_str}  每手入场费：{entry_str}")
        lines.append(f"   评分：{s['score']}/100 | 建议：{s['verdictLabel']}")
        lines.append(f"   ⚠️ 富途今日10:00截止申购，还有约20分钟！")
        lines.append("")
    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")

    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            print(f"[WARN] 飞书返回错误: {result}")
        else:
            print(f"[OK] 提醒已发送，涉及 {len(stocks_due)} 只股票")
    except Exception as e:
        print(f"[WARN] 飞书通知失败: {e}")


def main():
    today = datetime.date.today()
    print(f"[{datetime.datetime.now()}] 检查今日申购截止股票（{today}）...")

    data_path = os.path.abspath(DATA_JSON_PATH)
    with open(data_path, "r", encoding="utf-8") as f:
        stocks = json.load(f)

    stocks_due = []
    for s in stocks:
        # 跳过已上市、转板
        if s.get("status") in ("listed", "transfer"):
            continue
        end_date = parse_sub_end_date(s.get("subDate", ""))
        if end_date == today:
            stocks_due.append(s)
            print(f"  → {s['code']} {s['name']} 今日截止")

    if stocks_due:
        send_feishu_reminder(stocks_due, FEISHU_WEBHOOK)
    else:
        print("[OK] 今日无股票申购截止，不发提醒")


if __name__ == "__main__":
    main()
