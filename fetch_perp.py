#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_perp.py —— 仅补抓「保险公司永续债（无固定期限资本债券）」并合并进 ins_bonds.json。

实现说明:
  - 列表抓取走 akshare 的 bond_info_cm（chinamoney 列表接口）。注意: 直接裸 requests 打
    BondMarketInfoList2 现已被 chinamoney 返回 403，必须经 akshare（它自带正确的会话/Cookie）。
  - 详情抓取复用 fetch_ins_bonds.fetch_detail（裸 requests + DETAIL_HEADERS，已验证可用）。
  - 保留已有的记录（按 (发行人, 债券简称) 去重合并，与 fetch_ins_bonds.py 一致），
    只补充 chinamoney 上缺失的新发行债。注意：绝不能用 bondDefinedCode 去重——
    同一只债在 Excel 底库是 XLSX-xxx、在 akshare 是 ejgc... 类，code 不同会漏去重产生重复。
"""
import json
import os
import re
import sys
import time
from datetime import date

import fetch_ins_bonds as fb

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "ins_bonds.json")
PERP_TYPE = "无固定期限资本债券"
START_YEAR = 2019
END_YEAR = date.today().year

# akshare 列表接口偶有抖动，这里放慢节奏、避免触发 421 限流
LIST_SLEEP = 3.0
DETAIL_SLEEP = 1.5
YEAR_GAP = 5.0


def log(m):
    sys.stderr.write(m + "\n")
    sys.stderr.flush()


def fetch_list_year(year, retries=3):
    """走 akshare(chinamoney) 列表接口，返回该年全部记录；失败返回 []。"""
    last_err = None
    for i in range(1, retries + 1):
        try:
            return fb.fetch_list(PERP_TYPE, str(year))
        except Exception as e:  # noqa
            last_err = e
            log(f"  [retry] {year} list 第{i}次失败: {e}")
            time.sleep(3 * i)
    log(f"  [fail] {year} list 放弃: {last_err}")
    return []


def fetch_detail(code, retries=4):
    """复用 fetch_ins_bonds.fetch_detail，返回 bondBaseInfo dict 或 None。"""
    return fb.fetch_detail(code, retries=retries)


def _key(b):
    """与 fetch_ins_bonds.py 一致的去重键：发行人 + 债券简称。
    不能用 bondDefinedCode —— 同一只债在 Excel 底库(XLSX-xxx)与 akshare(ejgc... 类)下 code 不同，
    仅按 code 去重会漏掉、产生重复记录（历史上因此产生 25 组永续债重复）。"""
    return (str(b.get("issuer", "")).strip(), str(b.get("bondShort", "")).strip())


def main():
    # 载入已有，按 (发行人, 债券简称) 去重（与 fetch_ins_bonds.py 统一，避免跨源 code 不一致漏去重）
    seen = {}
    if os.path.exists(DATA_FILE):
        prev = json.load(open(DATA_FILE, encoding="utf-8"))
        for b in prev.get("bonds", []):
            k = _key(b)
            if k[0] or k[1]:
                seen[k] = b
    log(f"[merge] 载入已有 {len(seen)} 只")

    for yr in range(START_YEAR, END_YEAR + 1):
        log(f"[year] {yr}")
        rows = fetch_list_year(yr)
        time.sleep(LIST_SLEEP)
        if not rows:
            continue
        n_new = 0
        n_skip = 0
        for row in rows:
            issuer = row.get("发行人/受托机构") or ""
            if not fb.is_insurance(issuer):
                continue
            code = row.get("查询代码")
            if not code:
                continue
            info = fetch_detail(code)
            time.sleep(DETAIL_SLEEP)
            if not info:
                continue
            rec = fb.build_record(row, info)
            k = _key(rec)
            if k in seen:
                # 同名已存在（code 可能不同，如 XLSX vs akshare），仅做字段补全，避免重复计数
                for fld, val in rec.items():
                    if seen[k].get(fld) in (None, "") and val not in (None, ""):
                        seen[k][fld] = val
                n_skip += 1
                continue
            seen[k] = rec
            n_new += 1
        log(f"  {yr}: 本年新增 {n_new} 只, 跳过同名已存在 {n_skip} 只, 累计 {len(seen)} 只")

    bonds = list(seen.values())
    bonds.sort(key=lambda r: r.get("issueDate") or "", reverse=True)
    out = {
        "generatedAt": date.today().isoformat(),
        "source": "中国货币网(chinamoney) 债券信息综合查询",
        "count": len(bonds),
        "bonds": bonds,
    }
    json.dump(out, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    log(f"[done] 写出 {len(bonds)} 只")
    log("  按类型: " + str(dict(Counter(b["bondType"] for b in bonds))))
    log("  按状态: " + str(dict(Counter(b["status"] for b in bonds))))
    perp = [b for b in bonds if b.get("bondType") == "永续债"]
    by_year = {}
    for b in perp:
        y = (b.get("issueDate") or "")[:4]
        if not y:
            continue
        by_year[y] = by_year.get(y, 0) + (b.get("issueAmnt") or 0)
    log("  永续债各年发行额(亿元):")
    for y in sorted(by_year):
        log(f"    {y}: {by_year[y]:.2f}")


if __name__ == "__main__":
    main()
