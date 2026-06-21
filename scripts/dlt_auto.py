#!/usr/bin/env python3
"""
大乐透 V7 自动化脚本（端到端）
- 拉 500.com 50 期真实数据
- 算 V7 维度（前区+后区频次/遗漏/重号）
- 生成 5 注方案（V7 多约束：TOP6主力 + 温号补位 + 1重号防御 + 0重号 + 冷号博反弹）
- 5 个后区必须全不同（蓝球分散铁律）
- 复盘上一期 → 写 dlt_review_XXXXX.json
- 同步写入 dlt_latest_plans.json（复盘 cron 依赖此文件）

用法:
  python3 dlt_auto.py                       # 默认：拉数据 + 出下期方案 + 复盘上期
  python3 dlt_auto.py --next 26068          # 指定下期号
  python3 dlt_auto.py --review-only         # 只复盘上期
  python3 dlt_auto.py --next-only          # 只出下期方案
  python3 dlt_auto.py --backtest 20         # 跑 20 期回测（验证 V7）
  python3 dlt_auto.py --quiet               # 静默模式
"""
import urllib.request
import urllib.parse
import re
import json
import random
import sys
import os
import argparse
import subprocess
from collections import Counter
from datetime import datetime, date, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

# ============== 路径配置 ==============
HOME = Path.home()
REPO = HOME / 'code' / 'x-lottery'
REPORTS_DIR = REPO / 'reports'
REVIEW_DIR = REPO  # dlt_review_XXXXX.json 放这里
HERMES_PLANS = HOME / '.hermes' / 'scripts' / 'dlt_latest_plans.json'
HERMES_CHECK_LOG = HOME / '.hermes' / 'scripts' / 'dlt_check_log.json'
HERMES_LAST_CHECK = HOME / '.hermes' / 'scripts' / 'dlt_last_check_issue.txt'

# ============== 数据源（DLT 走 500.com，更稳）==============
DATA_URL_DLT = "https://datachart.500.com/dlt/history/newinc/history.php?start=26020&end={end}"
UA = "Mozilla/5.0"

# ============== 奖级规则（大乐透 2026 现行版）==============
PRIZE_TABLE = {
    (5, 2): (10000000, "一等奖"),
    (5, 1): (500000, "二等奖"),
    (5, 0): (10000, "三等奖"),
    (4, 2): (10000, "三等奖"),
    (4, 1): (3000, "四等奖"),
    (3, 2): (3000, "四等奖"),
    (4, 0): (300, "五等奖"),
    (3, 1): (300, "五等奖"),
    (2, 2): (300, "五等奖"),
    (3, 0): (15, "六等奖"),
    (2, 1): (15, "六等奖"),
    (1, 2): (15, "六等奖"),
    (0, 2): (15, "六等奖"),
}


def calc_prize(front_hit, back_hit):
    """单注中奖金额（2026 现行规则）。返回 (金额, 奖级)"""
    if front_hit < 0 or front_hit > 5 or back_hit < 0 or back_hit > 2:
        return (0, "无效")
    return PRIZE_TABLE.get((front_hit, back_hit), (0, "未中"))


# ============== 数据抓取 ==============
def fetch_dlt_history(limit=50):
    """从 500.com 抓取最近 limit 期大乐透数据（gb18030 解码，防字节丢失）"""
    # 先抓最大窗口，找到最新期号
    end_issue = 26999  # 远超当前期
    url = f"https://datachart.500.com/dlt/history/newinc/history.php?start=26000&end={end_issue}"
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode('gb18030', errors='ignore')

    soup = BeautifulSoup(raw, 'html.parser')
    tbody = soup.find('tbody', id='tdata')
    data = []
    for row in tbody.find_all('tr'):
        tds = row.find_all('td')
        if len(tds) >= 8:
            issue = tds[0].get_text(strip=True)
            if issue.isdigit() and len(issue) == 5:
                front_tds = row.find_all('td', class_='cfont2')
                back_tds = row.find_all('td', class_='cfont4')
                front = [int(td.get_text(strip=True)) for td in front_tds if td.get_text(strip=True).isdigit()]
                back = [int(td.get_text(strip=True)) for td in back_tds if td.get_text(strip=True).isdigit()]
                date = tds[-1].get_text(strip=True)
                if len(front) == 5 and len(back) == 2:
                    data.append({
                        'issue': issue,
                        'front': front,
                        'back': back,
                        'date': date,
                    })

    data.sort(key=lambda x: int(x['issue']), reverse=True)
    return data[:limit]


# ============== 维度计算 ==============
def get_omit(history, num=35, is_back=False):
    """算每个号码的遗漏期数（最新期不出现=遗漏1）"""
    omits = {i: 0 for i in range(1, num + 1)}
    for d in history:
        appeared = d['back'] if is_back else d['front']
        for n in omits:
            if n not in appeared:
                omits[n] += 1
    return omits


def get_freq(history, n_periods=20, is_back=False):
    """算近 n_periods 期出现频次"""
    cnt = Counter()
    for d in history[:n_periods]:
        cnt.update(d['back'] if is_back else d['front'])
    return cnt


# ============== V7 算法 ==============
def has_consec(nums):
    s = sorted(nums)
    return any(s[i + 1] - s[i] == 1 for i in range(len(s) - 1))


def has_two_same_tail(nums):
    t = [n % 10 for n in nums]
    return any(c >= 2 for c in Counter(t).values())


def make_v7_bets(history, n_bets=5):
    """V7 算法生成 5 注推荐（5+1+1，5 个后区全不同）"""
    if len(history) < 5:
        return [], {}, {}

    last_front = set(history[0]['front'])  # 最新期前区
    last_back = set(history[0]['back'])    # 最新期后区

    # 频次（20 期窗口）
    cnt_f_20 = get_freq(history, 20)
    cnt_b_20 = get_freq(history, 20, is_back=True)
    cnt_f_30 = get_freq(history, 30)
    # 遗漏（30 期窗口）
    omit_f = get_omit(history, 35)
    omit_b = get_omit(history, 12, is_back=True)

    # 前区 TOP6 综合（30 期频次降序，去上期）
    front_top6 = [n for n, _ in cnt_f_30.most_common(6) if n not in last_front]
    for n, _ in cnt_f_30.most_common(20):
        if len(front_top6) >= 6:
            break
        if n not in last_front and n not in front_top6:
            front_top6.append(n)

    # 前区热号（20 期 TOP15，去上期）
    front_hot = [n for n, _ in cnt_f_20.most_common(15) if n not in last_front]
    # 前区温号（遗漏 4-12 期）
    front_warm = sorted(
        [n for n, o in omit_f.items() if 4 <= o <= 12 and n not in last_front],
        key=lambda x: omit_f[x]
    )[:10]
    # 前区冷号（遗漏 15+ 期）
    front_cold = sorted(
        [n for n, o in omit_f.items() if o >= 15 and n not in last_front],
        key=lambda x: -omit_f[x]
    )[:8]

    # 后区热号 TOP5（杀上期）
    back_hot = [n for n, _ in cnt_b_20.most_common(7) if n not in last_back][:5]
    # 补全
    if len(back_hot) < 5:
        for n, _ in cnt_b_20.most_common(12):
            if n not in back_hot and n not in last_back:
                back_hot.append(n)
            if len(back_hot) >= 5:
                break
    back_hot = back_hot[:5]

    random.seed(int(datetime.now().timestamp()) % 100000)

    def gen_dlt(front_pool, n_repeat_max, back, max_try=3000):
        """DLT 单注：5+1"""
        actual_repeat = min(n_repeat_max, 2)
        candidates_in_pool = [n for n in last_front if n in front_pool]
        if actual_repeat > len(candidates_in_pool):
            actual_repeat = len(candidates_in_pool)
        fixed = random.sample(candidates_in_pool, actual_repeat) if candidates_in_pool and actual_repeat > 0 else []
        pool_clean = [n for n in front_pool if n not in fixed]
        for _ in range(max_try):
            if len(pool_clean) < 5 - len(fixed):
                return None
            rest = random.sample(pool_clean, 5 - len(fixed))
            front = sorted(fixed + rest)
            s = sum(front); span = max(front) - min(front)
            odd = sum(1 for n in front if n % 2 == 1)
            if not (60 <= s <= 130):
                continue
            if not (15 <= span <= 30):
                continue
            if odd not in (2, 3):
                continue
            if not has_two_same_tail(front):
                continue
            if not has_consec(front):
                continue
            return front
        return None

    plan_configs = [
        ('A TOP6 主力',         front_top6,                                0, back_hot[0]),
        ('B 4热+1温',           front_hot[:8] + front_warm,                0, back_hot[1]),
        ('C 含1重号',           list(last_front) + front_hot,              1, back_hot[2]),
        ('D 0重号防极端',       front_hot + front_warm,                    0, back_hot[3]),
        ('E 3热+2冷博反弹',     front_hot[:6] + front_cold[:3],            0, back_hot[4]),
    ]

    bets = []
    pool_summary = {
        'front_top6': front_top6,
        'front_hot': front_hot[:8],
        'front_warm_count': len(front_warm),
        'front_cold': front_cold[:4],
        'back_hot': back_hot,
    }
    for name, pool, n_rep, back in plan_configs[:n_bets]:
        front = gen_dlt(pool, n_rep, back)
        if front is None:
            # 兜底：放宽约束
            for _ in range(3000):
                front = sorted(random.sample(range(1, 36), 5))
                s = sum(front); span = max(front) - min(front)
                odd = sum(1 for n in front if n % 2 == 1)
                if 60 <= s <= 130 and 15 <= span <= 30 and odd in (2, 3):
                    break
        bets.append({
            'name': name,
            'front': front,
            'back': back,
            'front_str': ' '.join(f"{n:02d}" for n in front),
            'back_str': f"{back:02d}",
            'bet_str': f"{' '.join(f'{n:02d}' for n in front)} + {back:02d}",
            'n_repeat': sum(1 for n in front if n in last_front),
        })

    return bets, pool_summary, {
        'last_front': sorted(last_front),
        'last_back': sorted(last_back),
        'last_front_str': ' '.join(f"{n:02d}" for n in sorted(last_front)),
        'last_back_str': ' '.join(f"{n:02d}" for n in sorted(last_back)),
    }


# ============== 复盘 ==============
def review_period(period, recommendations, actual_draw):
    """复盘某一期"""
    win_front = set(actual_draw['front'])
    win_back = set(actual_draw['back'])

    results = []
    total_spend = 0
    total_earn = 0
    total_front_hits = 0
    back_hit_count = 0

    for plan in recommendations:
        front = plan['front']
        back = plan['back']
        fh = len(set(front) & win_front)
        bh = 1 if back in win_back else 0
        prize, level = calc_prize(fh, bh)

        total_spend += 2
        total_earn += prize
        total_front_hits += fh
        if bh:
            back_hit_count += 1

        results.append({
            'name': plan['name'],
            'front': front,
            'back': back,
            'front_hits': fh,
            'hit_front': sorted(set(front) & win_front),
            'back_hit': bool(bh),
            'prize': prize,
            'level': level,
        })

    summary = {
        'total_spend': total_spend,
        'total_earn': total_earn,
        'net': total_earn - total_spend,
        'actual_roi': f"{(total_earn - total_spend) / total_spend * 100:+.1f}%" if total_spend else "N/A",
        'total_front_hits': total_front_hits,
        'back_hit_count': back_hit_count,
        'back_hit_rate': f"{back_hit_count / len(recommendations) * 100:.0f}%",
        'front_hit_rate': f"{total_front_hits / (len(recommendations) * 5) * 100:.1f}%" if recommendations else "N/A",
    }

    return {'results': results, 'summary': summary}


# ============== 下一期期号计算 ==============
def predict_next_issue(history):
    """大乐透每周一/三/六开奖"""
    if not history:
        return None
    latest = history[0]
    latest_issue = int(latest['issue'])
    try:
        latest_date = datetime.strptime(latest['date'], "%Y-%m-%d")
    except (ValueError, TypeError):
        latest_date = datetime.now()

    # 大乐透 1=一, 3=三, 5=六
    days_ahead = 0
    while days_ahead < 7:
        next_date = latest_date + timedelta(days=days_ahead + 1)
        weekday = next_date.weekday()
        if weekday in [0, 2, 5]:
            break
        days_ahead += 1

    next_issue = str(latest_issue + 1).zfill(5)
    return {
        'issue': next_issue,
        'date': next_date.strftime("%Y-%m-%d"),
        'weekday': ['一', '二', '三', '四', '五', '六', '日'][next_date.weekday()],
    }


# ============== 报告输出 ==============
def format_plan_report(next_period, bets, pool_summary, context, history):
    """格式化方案报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history_range = f"{history[-1]['issue']}-{history[0]['issue']}"

    lines = [
        f"# 🎯 大乐透 {next_period['issue']} 期 V7 自动化方案",
        "",
        f"> **生成时间**：{now}",
        f"> **开奖日**：{next_period['date']} 周{next_period['weekday']} 21:30（停售 20:00）",
        f"> **数据基**：500.com {len(history)} 期（{history_range}）",
        f"> **最新已开**：{context['last_issue'] if 'last_issue' in context else history[0]['issue']}"
        f"（前 {context['last_front_str']} 后 {context['last_back_str']}）",
        "",
        "## 📊 候选池",
        "",
        f"- 前区 TOP6 综合：{pool_summary['front_top6']}",
        f"- 前区热号 TOP8：{pool_summary['front_hot']}",
        f"- 前区温号（遗漏 4-12 期）：{pool_summary['front_warm_count']} 个",
        f"- 前区冷号（遗漏 15+ 期）：{pool_summary['front_cold']}",
        f"- 后区热号 TOP5（杀上期）：{pool_summary['back_hot']}",
        "",
        "## 🎯 5 注推荐",
        "",
    ]

    for plan in bets:
        nums = plan['front']
        s = sum(nums); span = max(nums) - min(nums)
        odd = sum(1 for n in nums if n % 2 == 1)
        lines.append(f"**{plan['name']}**")
        lines.append(f"- 前: {plan['front_str']} | 后: **{plan['back_str']}**")
        lines.append(f"- 验证: 和值{s} 跨度{span} 奇偶{odd}:{5-odd} 含重号{plan['n_repeat']}个")
        lines.append("")

    lines.extend([
        "## 🎫 投注串（直接照抄到彩票单）",
        "",
        "```",
    ])
    for plan in bets:
        lines.append(f"{plan['name'].split()[0]}: {plan['bet_str']}")
    lines.extend([
        "```",
        "",
        "## 🚫 杀号清单",
        "",
        f"- **后区**：{context['last_back_str']}（上期刚出，连续两期同后<10%）",
        "",
        "## ⚠️ 重要提醒",
        "",
        "> **任何方案（包括 V7）的长期 ROI 仍为负**。",
        "> 单注期望收益 0.5 元，成本 2 元 → 长期 -75%。",
        "> **真正的「赚到钱」只能靠放弃买彩票**。",
        "",
    ])

    return "\n".join(lines)


def format_review_report(period, recommendations, actual, review):
    """格式化复盘报告"""
    lines = [
        f"# 📊 大乐透 {period} 期复盘（V7 自动化）",
        "",
        f"> **复盘时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 🎯 开奖号码",
        f"- 前区: {' '.join(f'{n:02d}' for n in actual['front'])}",
        f"- 后区: {' '.join(f'{n:02d}' for n in actual['back'])}",
        "",
        "## 📋 5 注逐注核对",
        "",
        "| 注 | 前区 | 后区 | 前中 | 后中 | 奖级 | 奖金 |",
        "|:---|:---|:---:|:---:|:---:|:---|---:|",
    ]

    for r in review['results']:
        front_str = r.get('front_str') or ' '.join(f'{n:02d}' for n in r['front'])
        back_str = r.get('back_str') or f"{r['back']:02d}"
        lines.append(
            f"| {r['name'].split()[0]} | {front_str} | {back_str} | "
            f"{r['front_hits']} | {'✓' if r['back_hit'] else '✗'} | {r['level']} | ¥{r['prize']} |"
        )

    s = review['summary']
    lines.extend([
        "",
        "## 💰 汇总",
        "",
        "| 指标 | 数值 |",
        "|:---|:---|",
        f"| 总投入 | ¥{s['total_spend']} |",
        f"| 总奖金 | ¥{s['total_earn']} |",
        f"| 净收益 | ¥{s['net']:+d} |",
        f"| 实际 ROI | {s['actual_roi']} |",
        f"| 前区命中数 | {s['total_front_hits']} / {len(review['results'])*5} |",
        f"| 后区命中 | {s['back_hit_count']} / {len(review['results'])} ({s['back_hit_rate']}) |",
        "",
    ])

    return "\n".join(lines)


# ============== 30 期回测 ==============
def backtest_v7(history, n_periods=20):
    """V7 在最近 n_periods 期的回测"""
    if len(history) < 16:
        return None

    n_periods = min(n_periods, len(history) - 15)
    if n_periods <= 0:
        return None

    stats = {'cost': 0, 'prize': 0, 'hits': 0, 'total': 0, 'fh': Counter(), 'back_hit': 0}

    for i in range(15, 15 + n_periods):
        future_history = history[i + 1:][::-1]  # 升序历史
        if len(future_history) < 5:
            continue

        win = history[i]
        bets, _, _ = make_v7_bets(future_history, n_bets=5)
        win_front = set(win['front'])
        win_back = set(win['back'])

        for plan in bets:
            if len(plan['front']) < 5:
                continue
            fh = len(set(plan['front']) & win_front)
            bh = 1 if plan['back'] in win_back else 0
            prize, _ = calc_prize(fh, bh)

            stats['cost'] += 2
            stats['prize'] += prize
            stats['fh'][fh] += 1
            if prize > 0:
                stats['hits'] += 1
            if bh:
                stats['back_hit'] += 1
            stats['total'] += 1

    return stats


# ============== 主入口 ==============
def main():
    parser = argparse.ArgumentParser(description='大乐透 V7 自动化')
    parser.add_argument('--next', type=str, help='指定下期期号（如 26068）')
    parser.add_argument('--review-only', action='store_true', help='只复盘上期')
    parser.add_argument('--next-only', action='store_true', help='只出下期方案')
    parser.add_argument('--backtest', type=int, help='跑 N 期回测')
    parser.add_argument('--history', type=int, default=50, help='拉多少期数据（默认 50）')
    parser.add_argument('--review-period', type=str, help='指定要复盘的期号')
    parser.add_argument('--quiet', action='store_true', help='静默模式')
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    HERMES_PLANS.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        if not args.quiet:
            print(msg)

    log(f"{'=' * 60}")
    log(f"大乐透 V7 自动化脚本")
    log(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'=' * 60}")

    # 1. 拉数据
    log("\n[1/4] 拉取 500.com 历史数据...")
    history = fetch_dlt_history(args.history)
    if not history:
        log("❌ 数据拉取失败，退出")
        return 1
    log(f"  ✅ 拿到 {len(history)} 期，{history[-1]['issue']} ~ {history[0]['issue']}")

    last_issue = history[0]['issue']

    # 2. 复盘上期
    if not args.next_only:
        period_to_review = args.review_period or last_issue
        log(f"\n[2/4] 复盘 {period_to_review} 期...")

        review_data = next((d for d in history if d['issue'] == period_to_review), None)
        if review_data is None:
            log(f"  ⚠️ 期号 {period_to_review} 不在数据中，跳过")
        else:
            review_idx = history.index(review_data)
            future_history = list(reversed(history[review_idx + 1:]))

            if len(future_history) < 5:
                log(f"  ⚠️ 历史数据不足 5 期，跳过")
            else:
                bets, _, ctx = make_v7_bets(future_history, n_bets=5)
                review = review_period(period_to_review, bets, review_data)

                # 写入 review json
                review_json = {
                    'period': period_to_review,
                    'date': review_data['date'],
                    'draw_result': {
                        'front': review_data['front'],
                        'back': review_data['back'],
                    },
                    'recommendation': {
                        'strategy': 'V7 (mult-constraint)',
                        'budget': '10元',
                        'plans': [
                            {'name': p['name'], 'front': p['front_str'].split(), 'back': [p['back_str']]}
                            for p in bets
                        ]
                    },
                    'results': review['results'],
                    'summary': review['summary'],
                    'review_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S CST'),
                }
                review_path = REVIEW_DIR / f"dlt_review_{period_to_review}.json"
                with open(review_path, 'w', encoding='utf-8') as f:
                    json.dump(review_json, f, ensure_ascii=False, indent=2)
                log(f"  ✅ 复盘报告: {review_path}")

                review_text = format_review_report(period_to_review, bets, review_data, review)
                log(review_text)

    if args.review_only:
        return 0

    # 3. 出下期方案
    log(f"\n[3/4] 生成下期方案...")
    bets, pool_summary, ctx = make_v7_bets(history, n_bets=5)

    next_issue = args.next or str(int(last_issue) + 1).zfill(5)
    next_date = predict_next_issue(history) or {'date': 'TBD', 'weekday': '?'}
    next_period = {
        'issue': next_issue,
        'date': next_date['date'] if isinstance(next_date, dict) else next_date,
        'weekday': next_date['weekday'] if isinstance(next_date, dict) else '?',
    }

    context = {
        'history_issue_range': f"{history[-1]['issue']}-{history[0]['issue']}",
        'last_issue': last_issue,
        'last_front_str': ctx['last_front_str'],
        'last_back_str': ctx['last_back_str'],
    }

    log(f"  ✅ 下一期: {next_issue} ({next_period['date']} 周{next_period['weekday']})")
    log(f"  后区候选 TOP5: {pool_summary['back_hot']}（杀 {' '.join(ctx['last_back_str'].split())}）")

    plan_text = format_plan_report(next_period, bets, pool_summary, context, history)
    log(plan_text)

    # 存档 markdown
    plan_path = REPORTS_DIR / f"dlt_{next_issue}.md"
    with open(plan_path, 'w', encoding='utf-8') as f:
        f.write(plan_text)
    log(f"\n  ✅ 方案存档: {plan_path}")

    # 同步写入 dlt_latest_plans.json（复盘 cron 依赖此文件）
    plans_for_hermes = {
        'period': next_issue,
        'date': next_period['date'],
        'draw_time': f"{next_period['date']} 21:30",
        'lottery': 'DLT',
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S CST'),
        'strategy': 'V7 (TOP6主力 + 温号补位 + 1重号防御 + 0重号 + 冷号博反弹)',
        'budget': '10元/期',
        'last_draw': {
            'issue': last_issue,
            'front': [f"{n:02d}" for n in history[0]['front']],
            'back': [f"{n:02d}" for n in history[0]['back']],
            'date': history[0]['date'],
        },
        'plans': [
            {'name': p['name'],
             'front': [f"{n:02d}" for n in p['front']],
             'back': [p['back_str']],
             'note': f"和{sum(p['front'])} 跨{max(p['front'])-min(p['front'])} 重{p['n_repeat']}"}
            for p in bets
        ],
        'kill_list': {
            'back': [f"{n:02d}" for n in history[0]['back']],
        },
        'pool': {
            'front_top6': [f"{n:02d}" for n in pool_summary['front_top6']],
            'front_hot': [f"{n:02d}" for n in pool_summary['front_hot']],
            'front_warm_count': pool_summary['front_warm_count'],
            'front_cold': [f"{n:02d}" for n in pool_summary['front_cold']],
            'back_hot': [f"{n:02d}" for n in pool_summary['back_hot']],
        },
    }
    with open(HERMES_PLANS, 'w', encoding='utf-8') as f:
        json.dump(plans_for_hermes, f, ensure_ascii=False, indent=2)
    log(f"  ✅ 复盘文件: {HERMES_PLANS}")

    # 4. 回测（如指定）
    if args.backtest:
        log(f"\n[4/4] {args.backtest} 期回测...")
        bt_stats = backtest_v7(history, args.backtest)
        if bt_stats:
            roi = (bt_stats['prize'] - bt_stats['cost']) / bt_stats['cost'] * 100
            log(f"  期数: {bt_stats['total'] // 5}")
            log(f"  总投入: ¥{bt_stats['cost']}")
            log(f"  总奖金: ¥{bt_stats['prize']}")
            log(f"  净: ¥{bt_stats['prize'] - bt_stats['cost']:+d}")
            log(f"  ROI: {roi:+.1f}%")
            log(f"  中奖率: {bt_stats['hits']}/{bt_stats['total']} = {bt_stats['hits'] / bt_stats['total'] * 100:.1f}%")
            log(f"  后区命中率: {bt_stats['back_hit'] / bt_stats['total'] * 100:.1f}%")
            log(f"  前区命中分布: {dict(bt_stats['fh'])}")

    log(f"\n{'=' * 60}")
    log(f"✅ 完成")
    log(f"{'=' * 60}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
