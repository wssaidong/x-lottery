#!/usr/bin/env python3
"""拉取彩票数据，更新 data/ 下的 JSON 和 CSV

支持: dlt (大乐透), ssq (双色球)

用法:
  python3 refresh_data.py                        # 默认: dlt 30期
  python3 refresh_data.py -g ssq                 # 双色球 30期
  python3 refresh_data.py -g dlt --count 50      # 大乐透 50期
  python3 refresh_data.py --all                  # 大乐透全部常用期数
  python3 refresh_data.py -g ssq --all           # 双色球全部常用期数
  python3 refresh_data.py --all --game both      # 两种彩都生成
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")

DEFAULT_COUNTS = [5, 10, 20, 30, 50, 100]

CSV_FIELDS_DLT = [
    "期号", "开奖日期", "前区", "后区",
    "前区号码1", "前区号码2", "前区号码3", "前区号码4", "前区号码5",
    "后区号码1", "后区号码2", "总销售额", "奖池余额",
]
CSV_FIELDS_SSQ = [
    "期号", "开奖日期", "红球", "蓝球",
    "红球号码1", "红球号码2", "红球号码3", "红球号码4", "红球号码5", "红球号码6",
    "蓝球号码1", "总销售额", "奖池余额",
]

DLT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SSQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://datachart.500.com/ssq/",
}


def fetch_dlt(count: int) -> list[dict]:
    url = (
        "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
        f"?gameNo=85&provinceId=0&pageNo=1&pageSize={count}&is498=true"
    )
    req = urllib.request.Request(url, headers=DLT_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())

    items = raw["value"]["list"]
    results: list[dict] = []
    for item in items:
        nums = item["lotteryDrawResult"].split()
        front = nums[:5]
        back = nums[5:]
        results.append({
            "期号": item["lotteryDrawNum"],
            "开奖日期": item["lotteryDrawTime"],
            "前区": ",".join(front),
            "后区": ",".join(back),
            "前区号码1": front[0], "前区号码2": front[1],
            "前区号码3": front[2], "前区号码4": front[3],
            "前区号码5": front[4],
            "后区号码1": back[0], "后区号码2": back[1],
            "总销售额": item["totalSaleAmount"],
            "奖池余额": item["poolBalanceAfterdraw"],
        })
    results.sort(key=lambda x: x["期号"])
    return results


def _ssq_periods() -> tuple[str, str]:
    url = "https://datachart.500.com/ssq/history/newinc/history.php?start=25001&end=26999"
    req = urllib.request.Request(url, headers=SSQ_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("gb2312", errors="replace")

    chart = re.search(r'class="chart">(.*?)</table>', html, re.S)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", chart.group(1), re.S)
    periods: list[str] = []
    for tr in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        vals = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
        if vals and len(vals) > 10 and vals[1].isdigit() and len(vals[1]) == 5:
            periods.append(vals[1])
    return periods[0], periods[-1]


def fetch_ssq(count: int) -> list[dict]:
    latest, _ = _ssq_periods()
    start = int(latest) - count + 1
    if start < 1:
        start = 1
    end = int(latest)

    url = f"https://datachart.500.com/ssq/history/newinc/history.php?start={start:05d}&end={end:05d}"
    req = urllib.request.Request(url, headers=SSQ_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("gb2312", errors="replace")

    chart = re.search(r'class="chart">(.*?)</table>', html, re.S)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", chart.group(1), re.S)
    results: list[dict] = []
    for tr in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        vals = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
        if not (vals and len(vals) >= 17 and vals[1].isdigit() and len(vals[1]) == 5):
            continue
        red = vals[2:8]
        blue = vals[8]
        results.append({
            "期号": vals[1],
            "开奖日期": vals[16],
            "红球": ",".join(red),
            "蓝球": blue,
            "红球号码1": red[0], "红球号码2": red[1],
            "红球号码3": red[2], "红球号码4": red[3],
            "红球号码5": red[4], "红球号码6": red[5],
            "蓝球号码1": blue,
            "总销售额": vals[10],
            "奖池余额": vals[15],
        })
    results.sort(key=lambda x: x["期号"])
    return results


def save(data: list[dict], game: str, count: int) -> None:
    fields = CSV_FIELDS_SSQ if game == "ssq" else CSV_FIELDS_DLT
    json_path = os.path.join(DATA_DIR, f"{game}_recent_{count}.json")
    csv_path = os.path.join(DATA_DIR, f"{game}_recent_{count}.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(data)
    print(f"  ✅ {count:3d}期 → {json_path}")


FETCHERS = {"dlt": fetch_dlt, "ssq": fetch_ssq}
NAMES = {"dlt": "大乐透", "ssq": "双色球"}


def run(game: str, count: int) -> bool:
    name = NAMES[game]
    print(f"拉取 {name} 最近 {count} 期...")
    try:
        data = FETCHERS[game](count)
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return False
    if not data:
        print("  ❌ 返回数据为空")
        return False
    latest = data[-1]
    front_key = "红球" if game == "ssq" else "前区"
    back_key = "蓝球" if game == "ssq" else "后区"
    print(f"  最新: 第{latest['期号']}期 ({latest['开奖日期']}) "
          f"{front_key} {latest[front_key]} + {back_key} {latest[back_key]}")
    os.makedirs(DATA_DIR, exist_ok=True)
    save(data, game, count)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新彩票数据")
    parser.add_argument("--game", "-g", choices=["dlt", "ssq", "both"], default="dlt",
                        help="彩种: dlt=大乐透, ssq=双色球, both=两种 (默认dlt)")
    parser.add_argument("--count", "-n", type=int, default=30, help="拉取期数 (默认30)")
    parser.add_argument("--all", "-a", action="store_true", help="生成所有常用期数")
    args = parser.parse_args()

    games = ["dlt", "ssq"] if args.game == "both" else [args.game]
    counts = DEFAULT_COUNTS if args.all else [args.count]

    for game in games:
        name = NAMES[game]
        print(f"\n{'='*40}")
        print(f" {name}数据刷新 期数: {', '.join(str(c) for c in counts)}")
        print(f"{'='*40}")
        ok = sum(1 for c in counts if run(game, c))
        print(f"完成 {ok}/{len(counts)} 个文件")


if __name__ == "__main__":
    main()
