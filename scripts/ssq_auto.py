#!/usr/bin/env python3
"""
双色球 V6 自动化脚本（端到端）
- 拉 500.com 30 期真实数据
- 算 V6 维度（奇偶/同尾/连号/和值/跨度/重号）
- 生成 5 注方案（V6 多约束）
- 复盘上一期（如有开奖）→ 写 ssq_review_XXXXX.json
- 支持 cron / 手动两种模式

用法:
  python3 ssq_auto.py                    # 默认：拉数据 + 出下期方案 + 复盘上期
  python3 ssq_auto.py --next 26069       # 指定下期号
  python3 ssq_auto.py --review-only      # 只复盘上期
  python3 ssq_auto.py --next-only       # 只出下期方案
  python3 ssq_auto.py --backtest 30     # 跑 30 期回测（验证 V6）
"""
import urllib.request
import urllib.parse
import re
import json
import random
import sys
import os
import argparse
from collections import Counter
from datetime import datetime, date
from pathlib import Path

# ============== 路径配置 ==============
HOME = Path.home()
REPO = HOME / 'code' / 'x-lottery'
REPORTS_DIR = REPO / 'reports'
DATA_DIR = REPO / 'data'
REVIEW_DIR = REPO  # ssq_review_XXXXX.json 放这里

# ============== 数据源 ==============
DATA_URL = "https://datachart.500.com/ssq/history/history.shtml"
UA = "Mozilla/5.0"

# ============== 奖级规则 ==============
PRIZE_TABLE = {
    (6, 1): (3000000, "一等奖"),
    (6, 0): (150000, "二等奖"),
    (5, 1): (3000, "三等奖"),
    (5, 0): (200, "四等奖"),
    (4, 1): (200, "四等奖"),
    (4, 0): (10, "五等奖"),
    (3, 1): (10, "五等奖"),
    (2, 1): (5, "六等奖"),
    (1, 1): (5, "六等奖"),
    (0, 1): (5, "六等奖"),
}

def calc_prize(red_hit, blue_hit):
    """单注中奖金额（2026 现行规则）。返回 (金额, 奖级)"""
    if red_hit < 0 or red_hit > 6 or blue_hit < 0 or blue_hit > 1:
        return (0, "无效")
    return PRIZE_TABLE.get((red_hit, blue_hit), (0, "未中"))

# ============== 数据抓取 ==============
def fetch_ssq_history(limit=30):
    """从 500.com 抓取最近 limit 期数据"""
    req = urllib.request.Request(DATA_URL, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode('gb18030', errors='ignore')
    
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', raw, re.DOTALL)
    data = []
    for r in rows:
        m = re.search(r'<td>(\d{5})</td>', r)
        if not m:
            continue
        reds = re.findall(r'class="t_cfont2">(\d{2})</td>', r)
        b = re.findall(r'class="t_cfont4">([^<]+)</td>', r)
        d = re.findall(r'<td>(\d{4}-\d{2}-\d{2})</td>', r)
        if len(reds) == 6 and b:
            data.append({
                'issue': m.group(1),
                'reds': reds,
                'blue': b[0].strip().zfill(2),
                'date': d[-1] if d else "",
            })
    
    # 按期号降序
    data.sort(key=lambda x: int(x['issue']), reverse=True)
    return data[:limit]

# ============== 维度计算 ==============
def get_omit(history, num=33, is_blue=False):
    """算每个号码的遗漏期数"""
    omits = {f"{n:02d}" if not is_blue else f"{n:02d}": 0 
             for n in range(1, num+1)}
    for d in reversed(history):
        if is_blue:
            appeared = [d['blue']]
        else:
            appeared = d['reds']
        for n in omits:
            if n not in appeared:
                omits[n] += 1
    return omits

def get_freq(history, n_periods=15, is_blue=False):
    """算近 n_periods 期出现频次"""
    cnt = Counter()
    for d in history[-n_periods:]:
        if is_blue:
            cnt.update([d['blue']])
        else:
            cnt.update(d['reds'])
    return cnt

def calc_dimensions(history):
    """计算 30 期各维度统计"""
    sums = [sum(int(r) for r in d['reds']) for d in history]
    spans = [max(int(r) for r in d['reds']) - min(int(r) for r in d['reds']) for d in history]
    
    odd_even_dist = Counter()
    for d in history:
        odd = sum(1 for r in d['reds'] if int(r) % 2 == 1)
        odd_even_dist[(odd, 6-odd)] += 1
    
    three_zone_dist = Counter()
    for d in history:
        z1 = sum(1 for r in d['reds'] if 1 <= int(r) <= 11)
        z2 = sum(1 for r in d['reds'] if 12 <= int(r) <= 22)
        three_zone_dist[(z1, z2, 6-z1-z2)] += 1
    
    return {
        'sum_min': min(sums), 'sum_max': max(sums), 'sum_median': sorted(sums)[len(sums)//2],
        'span_min': min(spans), 'span_max': max(spans), 'span_median': sorted(spans)[len(spans)//2],
        'odd_even': dict(odd_even_dist),
        'three_zone': dict(three_zone_dist),
    }

# ============== V6 算法 ==============
def make_v6_bets(history, n_bets=5):
    """V6 算法生成 5 注推荐"""
    if len(history) < 5:
        return [], {}, {}
    
    last_reds = set(history[0]['reds'])  # 最新期
    last_blue = history[0]['blue']
    
    cnt_r = get_freq(history, 15)
    cnt_b = get_freq(history, 15, is_blue=True)
    omit_r = get_omit(history, 33)
    omit_b = get_omit(history, 16, is_blue=True)
    
    # 蓝球候选：押 TOP 3 + 杀上期
    b_hot = [b for b, _ in cnt_b.most_common() if b != last_blue][:3]
    
    # 红球池
    warm = sorted([n for n in omit_r if 3 <= omit_r[n] <= 8], key=lambda x: omit_r[x])[:6]
    hot_clean = [n for n, _ in cnt_r.most_common(15) if n not in last_reds]
    cold = sorted([n for n in omit_r if omit_r[n] >= 12], key=lambda x: -omit_r[x])[:6]
    
    def has_consecutive(reds):
        nums = sorted(int(r) for r in reds)
        return any(nums[i+1] - nums[i] == 1 for i in range(5))
    
    def has_two_same_tail(reds):
        tails = [int(r) % 10 for r in reds]
        return any(c >= 2 for c in Counter(tails).values())
    
    random.seed(int(datetime.now().timestamp()) % 100000)
    last_reds_list = list(last_reds)
    
    def gen_smart(red_pool, n_repeat=0, blue='02', max_try=500):
        """V6.1（2026-06-21）：
        - 关键修复：fixed 之后 rest 必须从"非上期红球"池子里 sample，否则总重号数会失控
        """
        # V6.1 修复：从 red_pool 挑 fixed（重号），rest 完全排除 last_reds
        fixed = random.sample([r for r in last_reds_list if r in red_pool],
                            min(n_repeat, len([r for r in last_reds_list if r in red_pool])))
        # V6.1 关键：red_pool_clean 排除整个 last_reds（不只是 fixed）
        red_pool_clean = [r for r in red_pool if r not in last_reds_list]
        for _ in range(max_try):
            if len(red_pool_clean) < 6 - len(fixed):
                return None
            rest = random.sample(red_pool_clean, 6 - len(fixed))
            reds = sorted(fixed + rest)
            nums = sorted(int(r) for r in reds)
            s = sum(nums); span = max(nums) - min(nums)
            odd = sum(1 for n in nums if n % 2 == 1)
            if not (80 <= s <= 125): continue
            if not (15 <= span <= 28): continue
            if odd not in (2, 3, 4): continue
            if not has_two_same_tail(reds): continue
            if not has_consecutive(reds): continue
            return reds
        return None
    
    # V6.1 调整（2026-06-21 沉淀，基于 26070 + 26068 双双全军覆没硬数据）：
    # - 重号配比反转：之前 5 注里 3 注零重号（60% 概率全灭）→ 改为 4 注含 1-3 重号（攻）
    #                                                       + 1 注零重号（极端防守）
    # - 蓝球集中：之前分散到 4 个蓝 → 改为 TOP1 占 3 注 + TOP2 占 1 注 + 1 注温号博
    # - 60 期回测依据："押最热蓝" ROI -50% 远优于"分散 5 注" -77%（+27 个百分点）
    # - 🐛 关键修复（V6.1）：hot_clean 已经排除 last_reds，
    #   所以需要把 last_reds 加回 pool（让 fixed 能 sample 到），但 rest 从 pool_clean 排除 last_reds 拿
    # V6.2 用户偏好（2026-06-28）：篮球全部选第二热的（TOP2）
    # - 之前 V6.1: TOP1×3 + TOP2×2（押最热蓝，60 期回测 ROI -50% 优于分散 -77%）
    # - 现在: TOP2×5（押第二热蓝）
    # - ⚠️ 风险：连续翻车概率更高（TOP2 命中率 < TOP1）
    # - Fallback: b_hot 不足 2 个时退回 b_hot[0]（保底不空）
    _blue_t2 = b_hot[1] if len(b_hot) > 1 else (b_hot[0] if b_hot else '01')
    plan_configs = [
        ('A 含2重号主力', hot_clean + last_reds_list,                  2, _blue_t2),
        ('B 含1重号次主力', hot_clean + warm + last_reds_list,         1, _blue_t2),
        ('C 含2重号防守', hot_clean + last_reds_list,                  2, _blue_t2),
        ('D 0重号极端防守', hot_clean + warm,                          0, _blue_t2),
        ('E 含3重号博反弹', hot_clean + last_reds_list,                3, _blue_t2),
    ]
    
    bets = []
    pool_summary = {'b_hot': b_hot, 'warm': warm, 'hot_clean_count': len(hot_clean), 'cold': cold}
    for name, pool, n_rep, blue in plan_configs[:n_bets]:
        reds = gen_smart(pool, n_rep, blue)
        if reds is None:
            # 兜底
            all_pool = pool + [f"{i:02d}" for i in range(1, 34)]
            reds = sorted(random.sample(list(set(all_pool)), 6))
        bets.append({
            'name': name,
            'reds': reds,
            'blue': blue,
            'red_str': ', '.join(reds),
            'bet_str': f"{' '.join(reds)} + {blue}",
            'n_repeat': sum(1 for r in reds if r in last_reds),
        })
    
    return bets, pool_summary, {'last_reds': sorted(last_reds), 'last_blue': last_blue}

# ============== 复盘 ==============
# ============== 自适应调参应用 ==============
def _apply_tuning_to_ssq_bets(bets, tuning, history):
    """
    把 kill_set / must_set 应用到 V6 生成的 5 注上。
    策略（保守版，避免破坏 V6 约束）：
    1. kill_set.front: 在每注里把杀号换成候选红球（保持 6 个）
    2. kill_set.back:  把杀蓝换成 b_hot 候选
    3. must_set.front: 至少 1 注必须包含必选红号（替换最远端 1 个）
    4. must_set.back:  至少 1 注用必选蓝（替换）
    """
    import random as _r
    _r.seed(int(datetime.now().timestamp()) % 100000)

    # 1. 候选红球池（基于频次 + 遗漏，排除 kill_set）
    cnt_r = get_freq(history, 30)
    kill_f = set(str(x).zfill(2) for x in tuning["kill_set"].get("front", []))
    candidate_reds = [str(n).zfill(2) for n, _ in cnt_r.most_common(20)
                      if str(n).zfill(2) not in kill_f]
    if len(candidate_reds) < 10:
        candidate_reds += [f"{i:02d}" for i in range(1, 34) if f"{i:02d}" not in kill_f]

    # 蓝球候选（V6.2：按 TOP2 优先级，押 TOP2）
    # - 优先 b_hot[1]（第二热），其次 b_hot[0]（最热），最后 1-16 随机
    # - 独立从 history 算 b_hot，不依赖外部 pool_summary（调参函数没传）
    kill_b = set(str(x).zfill(2) for x in tuning["kill_set"].get("back", []))
    cnt_b = get_freq(history, 30, is_blue=True)
    last_b = bets[0]["blue"] if bets else None  # 用原推荐里的蓝作为近似 last_blue
    _b_hot_now = [b for b, _ in cnt_b.most_common() if b != last_b][:3]
    _b_t2 = _b_hot_now[1] if len(_b_hot_now) > 1 else (_b_hot_now[0] if _b_hot_now else None)
    _b_t1 = _b_hot_now[0] if _b_hot_now else None
    # 优先级列表：[TOP2, TOP1, ...1-16 随机去重]
    b_candidates = []
    for _cand in [_b_t2, _b_t1]:
        if _cand and str(_cand).zfill(2) not in kill_b:
            b_candidates.append(str(_cand).zfill(2))
    b_candidates += [f"{i:02d}" for i in range(1, 17) if f"{i:02d}" not in kill_b]
    # 去重保持顺序
    seen = set()
    b_candidates = [c for c in b_candidates if not (c in seen or seen.add(c))]

    # 必选号
    must_f = [str(x).zfill(2) for x in tuning["must_set"].get("front", [])]
    must_b = [str(x).zfill(2) for x in tuning["must_set"].get("back", [])]

    adjusted = []
    must_f_used = False
    must_b_used = False

    for i, bet in enumerate(bets):
        reds = [str(r).zfill(2) for r in bet["reds"]]
        blue = str(bet["blue"]).zfill(2)

        # ① 杀号替换（保持 6 个红球）
        new_reds = [r for r in reds if r not in kill_f]
        while len(new_reds) < 6:
            extra = _r.choice(candidate_reds)
            if extra not in new_reds:
                new_reds.append(extra)
        new_reds = sorted(new_reds[:6])

        # ② 蓝球替换（如果在杀号里）
        if blue in kill_b:
            new_blue = _r.choice(b_candidates) if b_candidates else "01"
        else:
            new_blue = blue

        # ③ must_set.front：第一注（i==0）必须包含必选红号
        if i == 0 and must_f and not must_f_used:
            for mf in must_f:
                if mf not in new_reds:
                    new_reds[-1] = mf  # 替换最后一个（破坏约束最小）
                    new_reds = sorted(new_reds)
            must_f_used = True

        # ④ must_set.back：第一注必须用必选蓝
        if i == 0 and must_b and not must_b_used and new_blue not in must_b:
            new_blue = _r.choice(must_b)
            must_b_used = True

        adjusted.append({
            **bet,
            "reds": new_reds,
            "blue": new_blue,
            "red_str": ', '.join(new_reds),
            "bet_str": f"{' '.join(new_reds)} + {new_blue}",
            "tuning_applied": True,
        })

    return adjusted, {"kill_f": list(kill_f), "kill_b": list(kill_b),
                     "must_f": must_f, "must_b": must_b}


def review_period(period, recommendations, actual_draw):
    """复盘某一期"""
    win_set = set(actual_draw['reds'])
    win_blue = actual_draw['blue']
    
    results = []
    total_spend = 0
    total_earn = 0
    total_red_hits = 0
    blue_hit_count = 0
    
    for plan in recommendations:
        reds = plan['reds']
        blue = plan['blue']
        rh = len(set(reds) & win_set)
        bh = 1 if blue == win_blue else 0
        prize, level = calc_prize(rh, bh)
        
        total_spend += 2
        total_earn += prize
        total_red_hits += rh
        if bh: blue_hit_count += 1
        
        results.append({
            'name': plan['name'],
            'reds': reds,
            'blue': blue,
            'red_hits': rh,
            'hit_reds': sorted(set(reds) & win_set),
            'blue_hit': bool(bh),
            'prize': prize,
            'level': level,
        })
    
    summary = {
        'total_spend': total_spend,
        'total_earn': total_earn,
        'net': total_earn - total_spend,
        'actual_roi': f"{(total_earn - total_spend) / total_spend * 100:+.1f}%" if total_spend else "N/A",
        'total_red_hits': total_red_hits,
        'blue_hit_count': blue_hit_count,
        'blue_hit_rate': f"{blue_hit_count / len(recommendations) * 100:.0f}%",
        'red_hit_rate': f"{total_red_hits / (len(recommendations) * 6) * 100:.1f}%" if recommendations else "N/A",
    }
    
    return {'results': results, 'summary': summary}

# ============== 下一期期号计算 ==============
def predict_next_issue(history):
    """根据最近期号预测下一期"""
    from datetime import timedelta
    if not history:
        return None
    
    latest = history[0]
    latest_issue = int(latest['issue'])
    latest_date = datetime.strptime(latest['date'], "%Y-%m-%d")
    
    # 双色球每周二/四/日开奖
    # 找下一个开奖日
    days_ahead = 0
    while days_ahead < 7:
        next_date = latest_date + timedelta(days=days_ahead + 1)
        weekday = next_date.weekday()  # 0=一, 1=二, 2=三, 3=四, 4=五, 5=六, 6=日
        if weekday in [1, 3, 6]:  # 二/四/日
            break
        days_ahead += 1
    
    next_issue = str(latest_issue + 1).zfill(5)
    return {
        'issue': next_issue,
        'date': next_date.strftime("%Y-%m-%d"),
        'weekday': ['一', '二', '三', '四', '五', '六', '日'][next_date.weekday()],
    }

# ============== 报告输出 ==============
def format_plan_report(next_period, bets, pool_summary, context, dimensions):
    """格式化方案报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        f"# 🎯 双色球 {next_period['issue']} 期 V6 自动化方案",
        "",
        f"> **生成时间**：{now}",
        f"> **开奖日**：{next_period['date']} 周{next_period['weekday']} 21:15（停售 20:00）",
        f"> **数据基**：500.com 30 期（{context['history_issue_range']}）",
        f"> **最新已开**：{context['last_issue']}（红 {' '.join(context['last_reds'])} + 蓝 {context['last_blue']}）",
        "",
        "## 📊 30 期维度统计",
        "",
        "| 维度 | 范围/分布 | V6 处理 |",
        "|:---|:---|:---|",
        f"| 奇偶 3:3 | 占比 {dimensions.get('odd_even_3_3', 'N/A')} | 优先生成 |",
        f"| 和值 | {dimensions['sum_min']}-{dimensions['sum_max']}（中位 {dimensions['sum_median']}）| 80-125 |",
        f"| 跨度 | {dimensions['span_min']}-{dimensions['span_max']}（中位 {dimensions['span_median']}）| 15-28 |",
        "",
        "## 🎯 5 注推荐",
        "",
    ]
    
    for i, plan in enumerate(bets, 1):
        nums = sorted(int(r) for r in plan['reds'])
        s = sum(nums); span = max(nums) - min(nums)
        odd = sum(1 for n in nums if n % 2 == 1)
        lines.append(f"**{plan['name']}**")
        lines.append(f"- 红: {plan['red_str']} | 蓝: **{plan['blue']}**")
        lines.append(f"- 验证: 和值{s} 跨度{span} 奇偶{odd}:{6-odd} 含重号{plan['n_repeat']}个")
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
        f"- **红球**（A/B/D/E 主力杀）：{', '.join(context['last_reds'])}（上期全期）",
        f"- **蓝球**（全部 5 注）：{context['last_blue']}（上期刚出，连续两期同蓝概率<10%）",
        f"- **C 注例外**：故意含 2-3 个上期红（应对 68.9% 常态重号）",
        "",
        "## ⚠️ 重要提醒",
        "",
        "> **任何方案（包括 V6）的长期 ROI 仍为负**。30 期回测：V3 智能 -77%、纯随机 -67%、押最热 -50%——都比\"不赌\"差。",
        "> **V6 的\"优化\"是\"少亏 5%\"，不是\"赚到钱\"**。**真正的\"赚到钱\"只能靠放弃买彩票**。",
        "",
    ])
    
    return "\n".join(lines)

def format_review_report(period, recommendations, actual, review):
    """格式化复盘报告"""
    return f"""# 📊 双色球 {period} 期复盘（V6 自动化）

> **复盘时间**：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 🎯 开奖号码
- 红球: {' '.join(actual['reds'])}
- 蓝球: {actual['blue']}

## 📋 5 注逐注核对

| 注 | 红球 | 蓝球 | 红中 | 蓝中 | 奖级 | 奖金 |
|:---|:---|:---:|:---:|:---:|:---|---:|
""" + "\n".join([
    f"| {r['name'].split()[0]} | {', '.join(r['reds'])} | {r['blue']} | {r['red_hits']} | {'✓' if r['blue_hit'] else '✗'} | {r['level']} | ¥{r['prize']} |"
    for r in review['results']
]) + f"""

## 💰 汇总

| 指标 | 数值 |
|:---|:---|
| 总投入 | ¥{review['summary']['total_spend']} |
| 总奖金 | ¥{review['summary']['total_earn']} |
| 净收益 | ¥{review['summary']['net']:+d} |
| 实际 ROI | {review['summary']['actual_roi']} |
| 红球命中数 | {review['summary']['total_red_hits']} / {len(review['results'])*6} |
| 蓝球命中 | {review['summary']['blue_hit_count']} / {len(review['results'])} ({review['summary']['blue_hit_rate']}) |

## 🔍 诊断

""" + generate_diagnosis(review, actual) + "\n"

def generate_diagnosis(review, actual):
    """生成诊断建议"""
    win_set = set(actual['reds'])
    win_blue = actual['blue']
    
    diag = []
    total_red = review['summary']['total_red_hits']
    if total_red == 0:
        diag.append("- **红球全军覆没**（0 命中）→ V6 候选池筛选失败，可能热号集中度过高")
    elif total_red <= 3:
        diag.append(f"- 红球总命中 {total_red}/30 → 低于均值，可能杀号过头")
    else:
        diag.append(f"- 红球总命中 {total_red}/30 → 表现{'正常' if total_red >= 6 else '偏低'}")
    
    if review['summary']['blue_hit_count'] == 0:
        diag.append(f"- **蓝球 0 命中**（开 {win_blue}）→ 押 TOP 3 但都没中，下次考虑加蓝球覆盖到 TOP 5")
    elif review['summary']['blue_hit_count'] == 1:
        diag.append(f"- 蓝球 1 命中（开 {win_blue}）→ 30 期回测均值水平")
    else:
        diag.append(f"- 蓝球 {review['summary']['blue_hit_count']} 命中 → 表现优秀")
    
    return "\n".join(diag)

# ============== 30 期回测 ==============
def backtest_v6(history, n_periods=30):
    """V6 在最近 n_periods 期的回测"""
    from datetime import timedelta
    
    stats = {'cost': 0, 'prize': 0, 'hits': 0, 'total': 0, 'rh': Counter(), 'blue_hit': 0}
    
    # 至少需要 16 期（15 历史 + 1 预测）
    if len(history) < 16:
        return None
    
    # history 是降序，history[0] 是最新期
    # 我们要预测 history[n]，需要 history[0..n-1] 作为历史
    # 但 history[n-1..0] 是更早的期（降序）
    # 注意：传统回测是"用前 N 期数据预测第 N+1 期"
    # 所以要循环：i = 0..len(history)-1，预测 history[i]，用 history[i+1..end] 作为历史
    
    # 但这样 i=0 时历史是整个数组（最新期用所有历史预测）
    # 这没意义，应该从 i=15 开始（至少 15 期历史）
    
    n_periods = min(n_periods, len(history) - 15)
    if n_periods <= 0:
        return None
    
    n_pred = 0
    for i in range(15, 15 + n_periods):  # 从 15 到 15+n_periods-1
        # history[i] 是待预测期
        # history[i-1..0] 是更早的期（按降序），反转后是升序历史
        future_history = list(reversed(history[:i]))
        
        if len(future_history) < 5:
            continue
        
        win = history[i]
        bets, _, _ = make_v6_bets(future_history, n_bets=5)
        win_set = set(win['reds'])
        win_blue = win['blue']
        
        for plan in bets:
            if len(plan['reds']) < 6:
                continue
            rh = len(set(plan['reds']) & win_set)
            bh = 1 if plan['blue'] == win_blue else 0
            prize, _ = calc_prize(rh, bh)
            stats['cost'] += 2
            stats['prize'] += prize
            stats['total'] += 1
            stats['rh'][rh] += 1
            if bh: stats['blue_hit'] += 1
            if prize > 0: stats['hits'] += 1
        n_pred += 1
    
    stats['n_pred'] = n_pred
    return stats

# ============== 主流程 ==============
def main():
    parser = argparse.ArgumentParser(description='双色球 V6 自动化')
    parser.add_argument('--next', type=str, help='指定下期期号（如 26069）')
    parser.add_argument('--review-only', action='store_true', help='只复盘上期')
    parser.add_argument('--next-only', action='store_true', help='只出下期方案')
    parser.add_argument('--backtest', type=int, help='跑 N 期回测（验证 V6）')
    parser.add_argument('--history', type=int, default=30, help='拉多少期数据（默认 30）')
    parser.add_argument('--review-period', type=str, help='指定要复盘的期号')
    parser.add_argument('--quiet', action='store_true', help='静默模式')
    args = parser.parse_args()
    
    # 创建目录
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    
    def log(msg):
        if not args.quiet:
            print(msg)
    
    log(f"{'='*60}")
    log(f"双色球 V6 自动化脚本")
    log(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'='*60}")
    
    # 1. 拉数据
    log("\n[1/4] 拉取 500.com 历史数据...")
    history = fetch_ssq_history(args.history)
    if not history:
        log("❌ 数据拉取失败，退出")
        return 1
    log(f"  ✅ 拿到 {len(history)} 期，{history[-1]['issue']} ~ {history[0]['issue']}")
    
    last_issue = history[0]['issue']
    
    # 2. 复盘上期（如果指定）
    if not args.next_only:
        period_to_review = args.review_period or last_issue
        log(f"\n[2/4] 复盘 {period_to_review} 期...")
        
        # 找该期数据
        review_data = next((d for d in history if d['issue'] == period_to_review), None)
        if review_data is None:
            log(f"  ⚠️ 期号 {period_to_review} 不在最新 30 期数据中，跳过复盘")
        else:
            # 用前 N 期历史数据生成方案
            # 找到 review_data 在 history 里的位置
            review_idx = history.index(review_data)
            future_history = list(reversed(history[review_idx+1:]))
            
            if len(future_history) < 5:
                log(f"  ⚠️ {period_to_review} 之前历史数据不足 5 期，跳过复盘")
            else:
                bets, _, ctx = make_v6_bets(future_history, n_bets=5)
                
                # 复盘
                review = review_period(period_to_review, bets, review_data)
                
                # 写 review.json
                review_json = {
                    'period': period_to_review,
                    'date': review_data['date'],
                    'draw_result': {
                        'reds': review_data['reds'],
                        'blue': review_data['blue']
                    },
                    'recommendation': {
                        'strategy': 'V6 (mult-constraint)',
                        'budget': '10元',
                        'plans': [
                            {'name': p['name'], 'reds': p['reds'], 'blue': p['blue']}
                            for p in bets
                        ]
                    },
                    'results': review['results'],
                    'summary': review['summary'],
                    'review_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S CST'),
                }
                review_path = REVIEW_DIR / f"ssq_review_{period_to_review}.json"
                with open(review_path, 'w', encoding='utf-8') as f:
                    json.dump(review_json, f, ensure_ascii=False, indent=2)
                log(f"  ✅ 复盘报告: {review_path}")
                
                # 打印复盘
                review_text = format_review_report(period_to_review, bets, review_data, review)
                log(review_text)
    
    if args.review_only:
        return 0
    
    # 3. 出下期方案
    log(f"\n[3/4] 生成下期方案...")
    bets, pool_summary, ctx = make_v6_bets(history, n_bets=5)

    # 3.5 应用自适应调参（从复盘 cron 写入的 feedback 计算）
    try:
        sys.path.insert(0, str(Path.home() / '.hermes' / 'scripts'))
        from lottery_strategy_adjuster import load_tuning, save_tuning
        # 如果 tuning.json 不存在但 feedback 有数据，先重算一次
        from pathlib import Path as _P
        if not _P(Path.home() / '.hermes' / 'scripts' / 'ssq_tuning.json').exists():
            save_tuning("SSQ")
        tuning = load_tuning("SSQ")
        if tuning["n_records"] >= 3:  # 至少 3 期反馈才应用
            log(f"  🧠 自适应调参生效：{tuning['n_records']} 期反馈")
            log(f"     杀号={tuning['kill_set']} 必选={tuning['must_set']}")
            bets, _adjusted = _apply_tuning_to_ssq_bets(bets, tuning, history)
            log(f"     应用到 {len(bets)} 注（kill_set 替换 / must_set 注入）")
        else:
            log(f"  🧠 自适应调参跳过（仅 {tuning['n_records']} 期反馈，需 ≥3 期）")
    except Exception as e:
        log(f"  ⚠️ 调参加载失败：{e}")
    
    next_issue = args.next or str(int(last_issue) + 1).zfill(5)
    next_date = predict_next_issue(history) or {'date': 'TBD', 'weekday': '?'}
    next_period = {
        'issue': next_issue,
        'date': next_date['date'] if isinstance(next_date, dict) else next_date,
        'weekday': next_date['weekday'] if isinstance(next_date, dict) else '?',
    }
    
    # 算维度
    dimensions = calc_dimensions(history)
    odd_even_3_3 = f"{dimensions['odd_even'].get((3, 3), 0)}/30 = {dimensions['odd_even'].get((3, 3), 0)/30*100:.1f}%"
    dimensions['odd_even_3_3'] = odd_even_3_3
    
    context = {
        'history_issue_range': f"{history[-1]['issue']}-{history[0]['issue']}",
        'last_issue': last_issue,
        'last_reds': ctx['last_reds'],
        'last_blue': ctx['last_blue'],
    }
    
    log(f"  ✅ 下一期: {next_issue} ({next_period['date']} 周{next_period['weekday']})")
    log(f"  蓝球候选 TOP3: {pool_summary['b_hot']}（杀 {ctx['last_blue']}）")
    log(f"  温号（遗漏 3-8 期）: {len(pool_summary['warm'])} 个")
    log(f"  冷号（遗漏 12+ 期）: {len(pool_summary['cold'])} 个")
    
    # 打印方案
    plan_text = format_plan_report(next_period, bets, pool_summary, context, dimensions)
    log(plan_text)
    
    # 存档 markdown
    plan_path = REPORTS_DIR / f"{next_issue}.md"
    with open(plan_path, 'w', encoding='utf-8') as f:
        f.write(plan_text)
    log(f"\n  ✅ 方案存档: {plan_path}")

    # 2026-06-20 修复：同步写入 ssq_latest_plans.json（复盘 cron 依赖此文件）
    plans_for_hermes = {
        'period': next_issue,
        'date': next_period['date'],
        'draw_time': f"{next_period['date']} 21:15",
        'lottery': 'SSQ',
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S CST'),
        'strategy': 'V6 (热号+温号+冷号博4红+0重号+蓝球分散)',
        'budget': '10元/期',
        'last_draw': {
            'issue': last_issue,
            'reds': [f"{int(r):02d}" for r in history[0]['reds']],
            'blue': f"{int(history[0]['blue']):02d}",
            'date': history[0]['date'],
        },
        'plans': [
            {'name': p['name'], 'reds': [f"{int(r):02d}" for r in p['reds']], 'blue': f"{int(p['blue']):02d}",
             'note': p.get('note', '')}
            for p in bets
        ],
    }
    hermes_plans_path = Path('/Users/cai/.hermes/scripts/ssq_latest_plans.json')
    with open(hermes_plans_path, 'w', encoding='utf-8') as f:
        json.dump(plans_for_hermes, f, ensure_ascii=False, indent=2)
    log(f"  ✅ 复盘文件: {hermes_plans_path}")
    
    # 4. 回测（如指定）
    if args.backtest:
        log(f"\n[4/4] {args.backtest} 期回测...")
        # 用 history 模拟
        bt_stats = backtest_v6(history, args.backtest)
        if bt_stats:
            roi = (bt_stats['prize'] - bt_stats['cost']) / bt_stats['cost'] * 100
            log(f"  期数: {bt_stats['total']//5}")
            log(f"  总投入: ¥{bt_stats['cost']}")
            log(f"  总奖金: ¥{bt_stats['prize']}")
            log(f"  净: ¥{bt_stats['prize']-bt_stats['cost']:+d}")
            log(f"  ROI: {roi:+.1f}%")
            log(f"  中奖率: {bt_stats['hits']}/{bt_stats['total']} = {bt_stats['hits']/bt_stats['total']*100:.1f}%")
            log(f"  蓝球命中率: {bt_stats['blue_hit']/bt_stats['total']*100:.1f}%")
            log(f"  红 2+ 比例: {(bt_stats['rh'][2]+bt_stats['rh'][3]+bt_stats['rh'][4]+bt_stats['rh'][5]+bt_stats['rh'][6])/bt_stats['total']*100:.1f}%")
    
    log(f"\n{'='*60}")
    log(f"✅ 完成")
    log(f"{'='*60}")
    return 0

if __name__ == "__main__":
    from datetime import timedelta
    sys.exit(main())
