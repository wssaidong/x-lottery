#!/usr/bin/env python3
"""
check_dlt_result.py — 大乐透开奖复盘 watchdog（2026-06-21 重建）

被 hermes cron job `958f800b2344` 调用（no_agent=True）。
输出：
- 人类可读复盘报告到 stdout（watchdog 投递到飞书 DM）
- [HERMES_OUTPUT]...[/HERMES_OUTPUT] JSON 块

数据源：
- 中彩网 JSONP API（transactionType=10001005, lotteryId=281，永远返回数据）
- 本地 ~/.hermes/scripts/dlt_latest_plans.json
"""
import json
import sys
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path


PLANS_FILE = Path.home() / ".hermes" / "scripts" / "dlt_latest_plans.json"
ZHCW_URL = "https://jc.zhcw.com/port/client_json.php"


def fetch_dlt_lottery_result(issue: str) -> dict:
    """从中彩网 JSONP API 抓取大乐透开奖结果（lotteryId=281）"""
    ts = str(int(time.time() * 1000))
    cb = f"cb_{ts[:-3]}"
    url = (
        f"{ZHCW_URL}?callback={cb}"
        f"&transactionType=10001005&lotteryId=281"
        f"&issue={issue}&_={ts}"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.zhcw.com/",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    # 中彩网返回的是混合数据，必须精确提取 lotteryId='281' 的对象
    # 用正则切片出 lotteryResults 里 lotteryId==281 的整个 {...}
    m = re.search(
        r'\{\s*"lotteryId"\s*:\s*"281"\s*,[^}]+\}',
        raw,
    )
    if not m:
        # fallback：直接全局字段提取（如果 lotteryId=281 是唯一对象）
        if '"lotteryId":"281"' not in raw:
            return {"issue": issue, "drawn": False, "raw": raw[:300]}
        block = raw
    else:
        block = m.group(0)

    fields = {}
    for key in ["issue", "openTime", "frontWinningNum", "backWinningNum"]:
        m2 = re.search(rf'"{key}"\s*:\s*"([^"]+)"', block)
        fields[key] = m2.group(1) if m2 else None

    if not fields.get("frontWinningNum"):
        return {"issue": issue, "drawn": False, "raw": raw[:300]}

    # 🐛 关键校验：中彩网 API 忽略 issue 参数，永远返回 lotteryId=281 最新已开期
    # 如果请求 issue 还没开（21:30 之后才有），API 会返回更早的期 → 必须拒绝，否则报告错位
    # 🐛 期号格式兼容：API 返回 "2026068"（年份+期号），plans 存 "26068"（5位期号）
    api_issue = fields["issue"]
    api_norm = api_issue[-5:] if len(api_issue) >= 5 else api_issue
    req_norm = issue[-5:] if len(issue) >= 5 else issue
    if api_norm != req_norm:
        # API 返回的是更早的期，说明请求的 issue 还没开奖
        return {
            "issue": issue,
            "drawn": False,
            "api_returned_issue": api_issue,
            "raw": f"API returned issue={api_issue} (not {issue}); {issue} likely not drawn yet",
        }

    fronts = fields["frontWinningNum"].split()
    # 大乐透前区必须是 5 个球（API 可能返回更多）
    if len(fronts) > 5:
        # 取前 5 个
        fronts = fronts[:5]
    backs = fields["backWinningNum"].split()
    # 大乐透后区必须是 2 个球
    if len(backs) > 2:
        backs = backs[:2]

    return {
        "issue": fields["issue"],
        "drawn": True,
        "fronts": fronts,
        "backs": backs,
        "date": fields.get("openTime", ""),
        "raw": "",
    }


def calc_prize_dlt(front_hit: int, back_hit: int) -> tuple:
    """大乐透 7 级固定奖级（2026 现行版）— 必跑逐注判断"""
    if front_hit == 5 and back_hit == 2:
        return (10000000, "一等奖")
    if front_hit == 5 and back_hit == 1:
        return (500000, "二等奖")
    if (front_hit == 5 and back_hit == 0) or (front_hit == 4 and back_hit == 2):
        return (10000, "三等奖")
    if (front_hit == 4 and back_hit == 1) or (front_hit == 3 and back_hit == 2):
        return (3000, "四等奖")
    if ((front_hit == 4 and back_hit == 0) or
        (front_hit == 3 and back_hit == 1) or
        (front_hit == 2 and back_hit == 2)):
        return (300, "五等奖")
    # 六等奖：3+0 / 2+1 / 1+2 / 0+2 都是 15 元（最容易漏算）
    if back_hit == 2 and front_hit in (0, 1, 2, 3):
        return (15, "六等奖")
    if (front_hit == 4 and back_hit == 0):
        return (5, "七等奖")
    return (0, "未中")


def load_plans() -> dict:
    if not PLANS_FILE.exists():
        return {}
    with open(PLANS_FILE) as f:
        return json.load(f)


def check_hit(plan_fronts: list, plan_backs: list,
              winning_fronts: list, winning_backs: list) -> dict:
    plan_f_set = set(plan_fronts)
    win_f_set = set(winning_fronts)
    plan_b_set = set(plan_backs)
    win_b_set = set(winning_backs)
    front_hit = len(plan_f_set & win_f_set)
    back_hit = len(plan_b_set & win_b_set)
    amount, level = calc_prize_dlt(front_hit, back_hit)
    return {
        "front_hit": front_hit,
        "back_hit": back_hit,
        "amount": amount,
        "level": level,
    }


def render_report(issue: str, draw_date: str, winning_fronts: list, winning_backs: list,
                  plans: list, results: list) -> str:
    total_cost = len(plans) * 2  # 2 元/注
    total_win = sum(r["amount"] for r in results)
    net = total_win - total_cost
    roi_pct = (net / total_cost * 100) if total_cost > 0 else 0

    has_win = any(r["amount"] > 0 for r in results)

    lines = []
    if has_win:
        lines.append(f"# ✅ 大乐透 {issue} 期复盘（已中奖）")
    else:
        lines.append(f"# ❌ 大乐透 {issue} 期复盘（未中奖）")

    lines.append("")
    lines.append(f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> **开奖日期**：{draw_date}")
    lines.append(f"> **开奖号码**：前 {' '.join(winning_fronts)} + 后 {' '.join(winning_backs)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📊 5 注逐注核对")
    lines.append("")
    lines.append("| 注 | 前区 | 后区 | 前中 | 后中 | 奖级 | 奖金 |")
    lines.append("|:--:|:--:|:--:|:--:|:--:|:--|--:|")
    for plan, res in zip(plans, results):
        lines.append(
            f"| {plan.get('name', '?')} | {' '.join(res['fronts'])} | {' '.join(res['backs'])} | "
            f"{res['front_hit']} | {res['back_hit']} | {res['level']} | ¥{res['amount']} |"
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
    lines.append("> ⚠️ **任何方案长期 ROI 仍为负**。大乐透期望更差（头奖概率 1/21425712）。")
    lines.append("> **数学结构决定长期必亏**。真正\"赚到钱\"的方式只有一种：放弃买彩票。")
    return "\n".join(lines)


def main():
    plans = load_plans()
    if not plans:
        print("# ❌ 大乐透复盘失败")
        print(f"未找到方案文件 {PLANS_FILE}")
        sys.exit(0)

    issue = plans["period"]

    try:
        draw = fetch_dlt_lottery_result(issue)
    except Exception as e:
        print(f"# ❌ 大乐透 {issue} 期复盘失败")
        print(f"抓取开奖结果出错：`{type(e).__name__}: {e}`")
        sys.exit(0)

    if not draw.get("drawn"):
        print(f"# ⏳ 大乐透 {issue} 期尚未开奖")
        print(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("停售 20:00，开奖 21:30")
        sys.exit(0)

    winning_fronts = draw["fronts"]
    winning_backs = draw["backs"]
    draw_date = draw["date"]

    results = []
    plans_list = plans.get("plans", [])
    for plan in plans_list:
        # DLT plans 用单数 front/back（兼容 SSQ reds/blue：自动 fallback）
        fronts = plan.get("fronts") or plan.get("front") or plan.get("reds", [])
        backs = plan.get("backs") or plan.get("back") or plan.get("blue", [])
        if isinstance(backs, str):
            backs = [backs]
        res = check_hit(fronts, backs, winning_fronts, winning_backs)
        res["name"] = plan.get("name", "?")
        res["fronts"] = fronts
        res["backs"] = backs
        results.append(res)

    report = render_report(issue, draw_date, winning_fronts, winning_backs,
                           plans_list, results)

    print(report)
    print("")

    has_win = any(r["amount"] > 0 for r in results)
    total_cost = len(plans.get("plans", [])) * 2
    total_win = sum(r["amount"] for r in results)
    winning_results = [
        {"name": r["name"], "front_hit": r["front_hit"], "back_hit": r["back_hit"],
         "level": r["level"], "amount": r["amount"]}
        for r in results if r["amount"] > 0
    ]

    hermes_json = {
        "issue": issue,
        "lottery": "DLT",
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