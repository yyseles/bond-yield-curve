#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性回填脚本：对 2025-12-31 及之后的所有日期，用中债网 bxjDownload 的高精度
（8 位小数百分比）国债即期 + MA750/MA60 重新抓取并覆盖 data.json 对应行。

背景：实测证明 bxjDownload 源本身即带 8 位小数精度，之前 data.json 只存 4 位是历史
陈旧数据。此脚本补齐 251231 至今的精度；日常 CI（update_gov_bond）已保留源精度，
新增日期自动高精度。此脚本只跑一次，不参与每日 CI。

用法：python backfill_precision.py
"""
import json
import os
import sys
import time
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ci", os.path.join(HERE, "ci_update.py"))
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

DATA_FILE = os.path.join(HERE, "data.json")
START = "2025-12-31"


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    dates = data["dates"]
    assert data["terms"] == ci.ALL_TERMS, "terms 不一致"
    if START not in dates:
        print(f"起始日 {START} 不在数据中，退出")
        return
    idx_start = dates.index(START)
    print(f"待回填 {len(dates) - idx_start} 个日期（{START} → {dates[-1]}）")

    upd = 0
    for i in range(idx_start, len(dates)):
        d = dates[i]
        try:
            g = ci.fetch_spot_rates_chinabond(d, csz="1")
            m750 = ci.fetch_spot_rates_chinabond(d, csz="750")
            m60 = ci.fetch_spot_rates_chinabond(d, csz="60")
        except Exception as e:
            print(f"  ⚠ {d}: 异常 {type(e).__name__}: {e}")
            time.sleep(1)
            continue
        if not g:
            print(f"  ⚠ {d}: 国债即期为空，跳过")
            continue
        data["rows"][i] = [g.get(t) for t in ci.ALL_TERMS]
        if m750:
            data["websiteMA750"][i] = [m750.get(t) for t in ci.ALL_TERMS]
        if m60:
            data["websiteMA60"][i] = [m60.get(t) for t in ci.ALL_TERMS]
        upd += 1
        if upd % 15 == 0:
            print(f"  …已更新 {upd} 个日期（至 {d}）")
        time.sleep(0.12)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"✅ 回填完成：更新 {upd} 个日期，范围 {START} → {dates[-1]}")
    # 抽查 251231
    i = dates.index(START)
    print(f"   251231 国债即期 1Y={data['rows'][i][0]} 10Y={data['rows'][i][9]} 50Y={data['rows'][i][49]}")
    print(f"   251231 MA750   1Y={data['websiteMA750'][i][0]} 50Y={data['websiteMA750'][i][49]}")


if __name__ == "__main__":
    main()
