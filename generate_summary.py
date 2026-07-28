"""
generate_summary.py
===================
生成首页 4 个汇总卡片使用的 summary.json。

数据源（4 个独立曲线文件，每文件结构 = {dates, terms, rows}）：
  data.json          → gov_spot  (国债即期)
  data_gov_ytm.json  → gov_ytm   (国债到期)
  data_cdb.json      → cdb_spot  (国开债即期)
  data_cdb_ytm.json  → cdb_ytm   (国开债到期)

输出：summary.json
{
  "date": "2026-07-27",                       // 最新交易日（4 曲线共同对齐）
  "generatedAt": "2026-07-28T15:25:00+08:00", // 生成时间 ISO
  "curves": {
    "gov_spot": {
      "name": "国债即期",
      "date": "2026-07-27",
      "terms": {
        "1Y":  {"value": 1.13891501, "change":  0.0017},   // change 单位 = 百分点（0.01 = 1bp）
        "5Y":  {"value": 1.44248251, "change": -0.0020},
        "10Y": {"value": 1.75428512, "change": -0.0009},
        "20Y": {"value": 2.2565,     "change": -0.0150},
        "30Y": {"value": 2.2485,     "change": -0.0200}
      }
    },
    ...
  }
}

每个 card 显示的 4 个子期限 = ['1Y', '5Y', '20Y', '30Y']，10Y 单独作 hero
（与 index.html 的 subTerms / htd = c.terms['10Y'] 保持一致）。

调用：
  python generate_summary.py              # 读当前 4 个 data 文件，写 summary.json
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# 4 曲线 → 数据文件 → 显示名
CURVES = [
    ('gov_spot', 'data.json',         '国债即期'),
    ('gov_ytm',  'data_gov_ytm.json', '国债到期'),
    ('cdb_spot', 'data_cdb.json',     '国开债即期'),
    ('cdb_ytm',  'data_cdb_ytm.json', '国开债到期'),
]

# 卡片需要显示的期限（1Y 5Y 20Y 30Y + 10Y 单独）
KEY_TERMS = ['1Y', '5Y', '10Y', '20Y', '30Y']


def load_curve(filename):
    """读 4 曲线之一的 {dates, terms, rows}"""
    with open(filename, 'r', encoding='utf-8') as f:
        d = json.load(f)
    if 'dates' not in d or 'rows' not in d or 'terms' not in d:
        raise ValueError(f'{filename} 结构不完整（缺 dates/terms/rows）')
    return d['dates'], d['terms'], d['rows']


def build_curve_summary(key, filename, display_name, ref_date=None):
    """从某曲线文件取 ref_date 当天的关键期限 value / change（前一个交易日）"""
    dates, terms, rows = load_curve(filename)

    # ref_date：默认该曲线最新一天；外部可强制统一（取 4 曲线共同最新）
    if ref_date is None:
        ref_date = dates[-1]

    if ref_date not in dates:
        raise ValueError(f'{filename} 中找不到日期 {ref_date}（最新 {dates[-1]}）')

    cur_idx = dates.index(ref_date)
    # 前一个交易日（数据集通常按交易日连续，往前找一个）
    prev_idx = cur_idx - 1 if cur_idx > 0 else None

    out_terms = {}
    for t in KEY_TERMS:
        if t not in terms:
            # 个别曲线可能缺某期限（如 sub-1Y），跳过
            out_terms[t] = {'value': None, 'change': None}
            continue
        ti = terms.index(t)
        cur_v = rows[cur_idx][ti]
        if cur_v is None:
            out_terms[t] = {'value': None, 'change': None}
            continue
        prev_v = rows[prev_idx][ti] if prev_idx is not None else None
        chg = (cur_v - prev_v) if (prev_v is not None) else None
        out_terms[t] = {'value': cur_v, 'change': chg}

    return {
        'name': display_name,
        'date': ref_date,
        'terms': out_terms,
    }


def common_latest_date(repo_dir):
    """取 4 曲线共同最新日期（min，避免 1 条曲线跳到未来导致 3 卡片用旧值）"""
    latest = None
    for _, fn, _ in CURVES:
        with open(os.path.join(repo_dir, fn), 'r', encoding='utf-8') as f:
            d = json.load(f)
        ld = d['dates'][-1]
        latest = ld if latest is None else (ld if ld < latest else latest)
    return latest


def main():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(repo_dir, 'summary.json')

    # 取 4 曲线共同最新一天
    ref = common_latest_date(repo_dir)
    print(f'[generate_summary] ref_date = {ref}')

    curves_out = {}
    for key, fn, name in CURVES:
        c = build_curve_summary(key, fn, name, ref_date=ref)
        curves_out[key] = c
        # 打印便于人工核对
        hero = c['terms'].get('10Y') or {}
        print(f'  {key:<8} date={c["date"]}  10Y={hero.get("value")}  chg={hero.get("change")}')

    payload = {
        'date': ref,
        'generatedAt': datetime.now(CST).strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'curves': curves_out,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    print(f'[generate_summary] wrote {out_path} ({os.path.getsize(out_path)} bytes)')


if __name__ == '__main__':
    main()
