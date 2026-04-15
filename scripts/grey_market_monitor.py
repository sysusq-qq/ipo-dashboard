#!/usr/bin/env python3
"""
暗盘实时监控脚本 (Grey Market Monitor)
-------------------------------------
在每只新股暗盘交易日 16:30–18:30 HKT 运行
- 通过 Futu OpenAPI 拉取实时暗盘价格
- 每10分钟发送一次飞书通知（含涨幅及交易建议）
- 18:30收盘后将最终价格写入 data.json 并发送收盘总结

依赖：
  pip install futu-api requests
  本机需运行 FutuOpenD（默认端口 11111）
"""

import json
import os
import sys
import time
import datetime
import requests

try:
    import futu as ft
    FUTU_OK = True
except ImportError:
    FUTU_OK = False
    print("[ERROR] futu-api 未安装，请运行: pip install futu-api")
    sys.exit(1)

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
FEISHU_WEBHOOK  = os.environ.get("FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/bfb0bc75-5d4f-4c88-b587-1f65ef62abbc")
DATA_JSON_PATH  = os.path.join(os.path.dirname(__file__), "..", "data.json")
FUTU_HOST       = "127.0.0.1"
FUTU_PORT       = 11111

GM_START_HM = 1614   # 16:14 HKT（暗盘 16:15 开盘前1分钟）
GM_END_HM   = 1830   # 18:30 HKT
POLL_INTERVAL = 300  # 轮询间隔（秒，5分钟）

# 暗盘交易建议矩阵
def get_advice(change_pct):
    """根据暗盘涨幅给出交易建议"""
    if change_pct is None:
        return "—", "gray"
    if change_pct >= 20:
        return "🚀 大幅溢价，可持仓至明日开市；量大则继续持有", "strong_buy"
    elif change_pct >= 10:
        return "✅ 溢价健康，建议持仓至明日开市", "buy"
    elif change_pct >= 5:
        return "👀 轻微溢价，视持仓成本决定是否持仓过夜", "watch"
    elif change_pct >= 0:
        return "⚠️ 微幅溢价/平开，谨慎持仓；孖展仓建议锁定收益", "caution"
    elif change_pct >= -5:
        return "❌ 小幅折价，孖展仓建议止损；现金仓可视情持有", "sell"
    else:
        return "🔴 大幅折价，建议止损离场", "stop_loss"


def get_beijing_now():
    """返回北京时间的 datetime 和 hhmm 整数"""
    bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    hhmm = bj.hour * 100 + bj.minute
    return bj, hhmm


def load_data():
    path = os.path.abspath(DATA_JSON_PATH)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(stocks):
    path = os.path.abspath(DATA_JSON_PATH)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)


def get_grey_market_stocks(stocks, today_str):
    """返回今日有暗盘交易的股票列表"""
    result = []
    for s in stocks:
        gm = s.get("greyMarket")
        if gm and gm.get("date") == today_str:
            result.append(s)
    return result


def fetch_futu_prices(codes_hk):
    """
    从 Futu OpenAPI 获取暗盘实时价格快照
    返回 {code_5digit: last_price} 字典
    codes_hk: ['HK.02476', ...]
    """
    prices = {}
    try:
        ctx = ft.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        ret, data = ctx.get_market_snapshot(codes_hk)
        ctx.close()

        if ret != ft.RET_OK:
            print(f"[WARN] Futu snapshot failed: {data}")
            return prices

        for _, row in data.iterrows():
            raw_code = row["code"]  # 'HK.02476'
            last_price = float(row.get("last_price", 0) or 0)
            code_5 = raw_code.replace("HK.", "").zfill(5)
            prices[code_5] = last_price

    except Exception as e:
        print(f"[WARN] fetch_futu_prices error: {e}")
    return prices


def send_feishu(text):
    """发送飞书消息，消息必须包含'IPO仪表盘'关键词"""
    if not FEISHU_WEBHOOK:
        return
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            print(f"[WARN] 飞书返回错误: {result}")
        else:
            print(f"[OK] 飞书通知已发送")
    except Exception as e:
        print(f"[WARN] 飞书通知失败: {e}")


def build_notification(gm_stocks, prices, is_final=False):
    """构建暗盘通知文本"""
    bj_now, _ = get_beijing_now()
    time_str = bj_now.strftime("%H:%M")
    today_str = bj_now.strftime("%Y-%m-%d")

    if is_final:
        header = f"📊 IPO仪表盘 暗盘收盘报告 [{today_str} 18:30 HKT]"
    else:
        header = f"📊 IPO仪表盘 暗盘实时更新 [{today_str} {time_str} HKT]"

    lines = [header, ""]

    for s in gm_stocks:
        code = s["code"]
        name = s["name"]
        ipo_price = s.get("price")
        last_price = prices.get(code)

        if last_price and last_price > 0 and ipo_price:
            change_val = last_price - ipo_price
            change_pct = change_val / ipo_price * 100
            sign = "+" if change_pct >= 0 else ""
            price_line = f"HK${last_price:.2f}  ({sign}{change_pct:.1f}%)"
            advice, _ = get_advice(change_pct)
        elif last_price == 0 or last_price is None:
            change_pct = None
            price_line = "暂无报价"
            advice = "等待开盘..."
        else:
            change_pct = None
            price_line = f"HK${last_price:.2f}"
            advice = "—"

        verdict_emoji = "✅" if s.get("verdict") == "da" else ("👀" if s.get("verdict") == "watch" else "❌")
        lines.append(f"{verdict_emoji} {code} {name}")
        lines.append(f"   IPO价：HK${ipo_price}  暗盘：{price_line}")
        lines.append(f"   建议：{advice}")
        lines.append("")

    lines.append("🔗 https://sysusq-qq.github.io/ipo-dashboard/")
    return "\n".join(lines)


def main():
    bj_now, hhmm = get_beijing_now()
    today_str = bj_now.strftime("%Y-%m-%d")
    print(f"[{bj_now.strftime('%Y-%m-%d %H:%M:%S')} BJ] 暗盘监控启动")

    # ── 加载数据，找今日暗盘股票
    stocks = load_data()
    gm_stocks = get_grey_market_stocks(stocks, today_str)

    if not gm_stocks:
        print(f"[OK] 今日 ({today_str}) 无暗盘交易股票，退出")
        return

    names = [f"{s['code']} {s['name']}" for s in gm_stocks]
    print(f"[OK] 今日暗盘股票: {names}")

    codes_hk = [f"HK.{s['code']}" for s in gm_stocks]

    # ── 等待暗盘开始
    if hhmm < GM_START_HM:
        wait_sec = (16 * 60 + 30 - bj_now.hour * 60 - bj_now.minute) * 60 - bj_now.second
        print(f"[OK] 等待暗盘开始（{wait_sec//60}分钟后）...")
        time.sleep(max(0, wait_sec))

    # ── 轮询循环
    sent_open_notice = False
    last_prices = {}

    while True:
        bj_now, hhmm = get_beijing_now()

        if hhmm >= GM_END_HM:
            # 收盘处理
            print(f"[{bj_now.strftime('%H:%M')}] 暗盘交易结束，生成收盘报告...")
            final_prices = fetch_futu_prices(codes_hk)

            # 更新 data.json
            updated = False
            for s in stocks:
                code = s["code"]
                gm = s.get("greyMarket")
                if gm and gm.get("date") == today_str:
                    last_p = final_prices.get(code)
                    ipo_p  = s.get("price")
                    if last_p and last_p > 0 and ipo_p:
                        gm["price"]     = round(last_p, 3)
                        gm["changePct"] = round((last_p - ipo_p) / ipo_p * 100, 2)
                        updated = True
                        print(f"  → {code}: 最终暗盘价 HK${last_p:.3f}  ({gm['changePct']:+.2f}%)")

            if updated:
                save_data(stocks)
                print("[OK] data.json 已更新暗盘最终数据")

            # 发收盘通知
            notice = build_notification(gm_stocks, final_prices, is_final=True)
            send_feishu(notice)
            break

        # 开盘首次通知
        if not sent_open_notice:
            print(f"[{bj_now.strftime('%H:%M')}] 暗盘开市，发送开盘通知...")
            prices = fetch_futu_prices(codes_hk)
            last_prices = prices
            notice = build_notification(gm_stocks, prices, is_final=False)
            send_feishu(notice)
            sent_open_notice = True
        else:
            prices = fetch_futu_prices(codes_hk)
            # 只在价格有变化时发通知（避免重复刷屏）
            price_changed = any(
                prices.get(s["code"], 0) != last_prices.get(s["code"], 0)
                for s in gm_stocks
            )
            if price_changed:
                print(f"[{bj_now.strftime('%H:%M')}] 价格变动，发送更新通知...")
                notice = build_notification(gm_stocks, prices, is_final=False)
                send_feishu(notice)
                last_prices = prices
            else:
                print(f"[{bj_now.strftime('%H:%M')}] 价格无变化，跳过通知")

        # 智能睡眠：距收盘不足一个轮询周期时，直接睡到18:30，确保准时收盘
        bj_tmp, _ = get_beijing_now()
        secs_to_close = (
            (GM_END_HM // 100) * 3600 + (GM_END_HM % 100) * 60
            - bj_tmp.hour * 3600 - bj_tmp.minute * 60 - bj_tmp.second
        )
        time.sleep(min(POLL_INTERVAL, max(5, secs_to_close)))

    print("[OK] 暗盘监控完成")


if __name__ == "__main__":
    main()
