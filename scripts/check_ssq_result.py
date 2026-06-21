#!/usr/bin/env python3
"""
check_ssq_result.py — 双色球开奖复盘 watchdog（2026-06-21 重建）

被 hermes cron job `5f1f47bd8a51` 调用（no_agent=True）。
输出：
- 人类可读复盘报告到 stdout（watchdog 投递到飞书 DM）
- [HERMES_OUTPUT]...[/HERMES_OUTPUT] JSON 块（机器解析，含 report/has_win/winning_results/issue）

数据源：
- 中彩网 JSONP API（transactionType=10001005，永远返回数据）
- 本地 ~/.hermes/scripts/ssq_latest_plans.json（上期方案）

输出顺序：
1. 第一行 # ✅/❌ 双色球 XXXXX 期复盘
2. 人类报告
3. [HERMES_OUTPUT]...[/HERMES_OUTPUT] JSON
"""
import json
import sys
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path


PLANS_FILE = Path.home() / ".hermes" / "scripts" / "ssq_latest_plans.json"
ZHCW_URL = "https://jc.zhcw.com/port/client_json.php"


def fetch_ssq_lottery_result(issue: str) -> dict:
    """从中彩网 JSONP API 抓取双色球开奖结果（transactionType=10001005，永远返回数据）"""
    ts = str(int(time.time() * 1000))
    cb = f"cb_{ts[:-3]}"
    url = (
        f"{ZHCW_URL}?callback={cb}"
        f"&transactionType=10001005&lotteryId=1"
        f"&issue={issue}&_={ts}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.zhcw.com/",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    # JSONP: cb_xxx({"lotteryResults":[...]})
    # 用正则字段提取，比 json.loads 更可靠
    fields = {}
    for key in ["issue", "openTime", "frontWinningNum", "backWinningNum"]:
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', raw)
        fields[key] = m.group(1) if m else None

    # 如果 frontWinningNum 缺失，说明该期未开奖
    if not fields.get("frontWinningNum"):
        return {"issue": issue, "drawn": False, "raw": raw[:300]}

    # 🐛 关键校验：中彩网 API 忽略 issue 参数，永远返回 lotteryId=1 最新已开期
    # 如果请求 issue 还没开（21:15 之后才有），API 会返回更早的期 → 必须拒绝，否则报告错位
    # 🐛 期号格式兼容：API 返回 "2026070"（年份+期号），plans 存 "26070"（5位期号）
    api_issue = fields["issue"]
    api_norm = api_issue[-5:] if len(api_issue) >= 5 else api_issue
    req_norm = issue[-5:] if len(issue) >= 5 else issue
    if api_norm != req_norm:
        return {
            "issue": issue,
            "drawn": False,
            "api_returned_issue": api_issue,
            "raw": f"API returned issue={api_issue} (not {issue}); {issue} likely not drawn yet",
        }

    return {
        "issue": fields["issue"],
        "drawn": True,
        "reds": fields["frontWinningNum"].split(),
        "blue": fields["backWinningNum"],
        "date": fields.get("openTime", ""),
        "raw": "",
    }


def calc_prize_ssq(red_hit: int, blue_hit: int) -> tuple:
    """双色球 6 级奖级（2026 现行版）— 必跑逐注判断"""
    if red_hit == 6 and blue_hit == 1:
        return (3000000, "一等奖")
    if red_hit == 6 and blue_hit == 0:
        return (150000, "二等奖")
    if red_hit == 5 and blue_hit == 1:
        return (3000, "三等奖")
    if (red_hit == 5 and blue_hit == 0) or (red_hit == 4 and blue_hit == 1):
        return (200, "四等奖")
    if (red_hit == 4 and blue_hit == 0) or (red_hit == 3 and blue_hit == 1):
        return (10, "五等奖")
    # 六等奖：2+1 / 1+1 / 0+1 都是 5 元（最容易漏算）
    if blue_hit == 1 and red_hit in (0, 1, 2):
        return (5, "六等奖")
    return (0, "未中")


def load_plans() -> dict:
    if not PLANS_FILE.exists():
        return {}
    with open(PLANS_FILE) as f:
        return json.load(f)


def check_hit(plan_reds: list, plan_blue: str, winning_reds: list, winning_blue: str) -> dict:
    plan_red_set = set(plan_reds)
    win_red_set = set(winning_reds)
    red_hit = len(plan_red_set & win_red_set)
    blue_hit = 1 if plan_blue == winning_blue else 0
    amount, level = calc_prize_ssq(red_hit, blue_hit)
    return {
        "red_hit": red_hit,
        "blue_hit": blue_hit,
        "amount": amount,
        "level": level,
    }


def render_report(issue: str, draw_date: str, winning_reds: list, winning_blue: str,
                  plans: list, results: list) -> str:
    """渲染人类可读复盘报告"""
    total_cost = len(plans) * 2  # 2 元/注
    total_win = sum(r["amount"] for r in results)
    net = total_win - total_cost
    roi_pct = (net / total_cost * 100) if total_cost > 0 else 0

    has_win = any(r["amount"] > 0 for r in results)

    lines = []
    if has_win:
        lines.append(f"# ✅ 双色球 {issue} 期复盘（已中奖）")
    else:
        lines.append(f"# ❌ 双色球 {issue} 期复盘（未中奖）")

    lines.append("")
    lines.append(f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> **开奖日期**：{draw_date}")
    lines.append(f"> **开奖号码**：红 {' '.join(winning_reds)} + 蓝 {winning_blue}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📊 5 注逐注核对")
    lines.append("")
    lines.append("| 注 | 红球 | 蓝 | 红中 | 蓝中 | 奖级 | 奖金 |")
    lines.append("|:--:|:--:|:--:|:--:|:--:|:--|--:|")
    for plan, res in zip(plans, results):
        lines.append(
            f"| {plan.get('name', '?')} | {' '.join(plan['reds'])} | {plan['blue']} | "
            f"{res['red_hit']} | {res['blue_hit']} | {res['level']} | ¥{res['amount']} |"
        )

    lines.append("")
    lines.append("## 💰 汇总")
    lines.append("")
    lines.append(f"- **总投入**：¥{total_cost}（{len(plans)} 注 × 2 元）")
    lines.append(f"- **总奖金**：¥{total_win}")
    lines.append(f"- **净收益**：¥{net:+d}")
    lines.append(f"- **ROI**：{roi_pct:+.1f}%")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ **任何方案长期 ROI 仍为负**（60 期回测 -77% ~ -83%）。")
    lines.append("> 单注期望 0.5 元 / 成本 2 元，**数学结构决定长期必亏**。")
    lines.append("> 真正\"赚到钱\"的方式只有一种：放弃买彩票。")
    return "\n".join(lines)


def main():
    plans = load_plans()
    if not plans:
        print("# ❌ 双色球复盘失败")
        print(f"未找到方案文件 {PLANS_FILE}")
        # watchdog: 非空 stdout → 投递
        sys.exit(0)

    issue = plans["period"]

    # 1. 抓开奖结果
    try:
        draw = fetch_ssq_lottery_result(issue)
    except Exception as e:
        print(f"# ❌ 双色球 {issue} 期复盘失败")
        print(f"抓取开奖结果出错：`{type(e).__name__}: {e}`")
        print("可能原因：网络问题 / 中彩网 API 临时不可用")
        sys.exit(0)

    if not draw.get("drawn"):
        # 该期未开奖（21:15 之后才会开）
        print(f"# ⏳ 双色球 {issue} 期尚未开奖")
        print(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("停售 20:00，开奖 21:15")
        sys.exit(0)

    winning_reds = draw["reds"]
    winning_blue = draw["blue"]
    draw_date = draw["date"]

    # 2. 逐注核对
    results = []
    for plan in plans.get("plans", []):
        res = check_hit(plan["reds"], plan["blue"], winning_reds, winning_blue)
        res["name"] = plan.get("name", "?")
        results.append(res)

    # 3. 渲染报告
    report = render_report(issue, draw_date, winning_reds, winning_blue,
                           plans.get("plans", []), results)

    # 4. 人类报告
    print(report)
    print("")

    # 5. [HERMES_OUTPUT] JSON 块
    has_win = any(r["amount"] > 0 for r in results)
    total_cost = len(plans.get("plans", [])) * 2
    total_win = sum(r["amount"] for r in results)
    winning_results = [
        {"name": r["name"], "red_hit": r["red_hit"], "blue_hit": r["blue_hit"],
         "level": r["level"], "amount": r["amount"]}
        for r in results if r["amount"] > 0
    ]

    hermes_json = {
        "issue": issue,
        "lottery": "SSQ",
        "has_win": has_win,
        "winning_results": winning_results,
        "total_cost": total_cost,
        "total_win": total_win,
        "net": total_win - total_cost,
        "report": report,
    }
    print("[HERMES_OUTPUT]")
    print(json.dumps(hermes_json, ensure_ascii=False))
    print("[/HERMES_OUTPUT]")

    sys.exit(0)


if __name__ == "__main__":
    main()