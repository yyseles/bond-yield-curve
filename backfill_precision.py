#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性回填脚本：对中债网 bxjDownload 返回的高精度（8 位小数百分比）国债即期 +
MA750/MA60 重新抓取并覆盖 data.json 的低精度行，使全序列达到源精度。

背景：实测证明 bxjDownload 源本身即带 8 位小数精度，之前 data.json 只存 4 位是历史
陈旧数据。日常 CI（update_gov_bond）已保留源精度，新增日期自动高精度。

本脚本「智能回填」：仅对 2024-01-01 起、当前仍为低精度（≤4 位）的日期重抓覆盖，
已高精度的（如 2026 全量、2025-12-31）直接跳过，省时且安全。不参与每日 CI。

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
MIN_START = "2022-01-01"   # 回填 2022 起低精度行（2024+ 已高精度自动跳过）


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    dates = data["dates"]
    assert data["terms"] == ci.ALL_TERMS, "terms 不一致"

    cand = 0
    for i, d in enumerate(dates):
        if d < MIN_START:
            continue
        v = data["rows"][i][0]
        dec = len(str(v).split(".")[1]) if "." in str(v) else 0
        if dec > 4:
            continue
        cand += 1
    print(f"待回填 {cand} 个低精度日期（{MIN_START} 起）")

    upd = 0
    for i, d in enumerate(dates):
        if d < MIN_START:
            continue
        v = data["rows"][i][0]
        dec = len(str(v).split(".")[1]) if "." in str(v) else 0
        if dec > 4:
            continue
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
        if upd % 20 == 0:
            print(f"  …已更新 {upd} 个日期（至 {d}）")
        time.sleep(0.12)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"✅ 回填完成：更新 {upd} 个日期（{MIN_START} 起的低精度行）")


if __name__ == "__main__":
    main()
