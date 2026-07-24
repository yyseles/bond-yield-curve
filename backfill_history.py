#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史数据回填脚本（2008 → 现有最早日期，倒序）
复用 ci_update.py 的抓取/解析/重试逻辑，按交易日逐日向前补，定期原子落盘（断点续传）。

覆盖 4 条曲线：
  data.json        - 国债即期 (csz=1)；历史段 MA750/MA60 暂置 null（二期再补，前端自动回退重算）
  data_gov_ytm     - 国债到期 (searchYc GOV qxll=0)
  data_cdb         - 国开即期 (searchYc CDB qxll=1)
  data_cdb_ytm     - 国开到期 (searchYc CDB qxll=0)

注意：早期（约 2008-2011）中债曲线最长仅到 30Y，40Y/50Y 会为 null（前端按缺失处理）。
"""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_update as C

START = "2008-01-01"
SLEEP = 0.15  # 请求间隔，避免触发限流


def _save_spot(file, dtr, dm750, dm60):
    sd = sorted(dtr.keys())
    C.save_json(file, {
        "dates": sd, "terms": C.ALL_TERMS,
        "rows": [dtr[x] for x in sd],
        "websiteMA750": [dm750.get(x) for x in sd],
        "websiteMA60": [dm60.get(x) for x in sd],
    })


def backfill_gov_spot():
    print("\n" + "=" * 50)
    print("  [国债即期] 历史回填 (spot only, MA 二期补)")
    print("=" * 50)
    existing = C.load_existing(C.DATA_FILE)
    if not existing["dates"]:
        print("  无现有数据，跳过"); return
    first = existing["dates"][0]
    print(f"  当前最早 {first} → 向 {START} 回填")
    start = datetime.strptime(START, "%Y-%m-%d")
    d = datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)
    dtr = {existing["dates"][i]: existing["rows"][i] for i in range(len(existing["dates"]))}
    dm750 = {existing["dates"][i]: existing["websiteMA750"][i] for i in range(len(existing["dates"]))}
    dm60 = {existing["dates"][i]: existing["websiteMA60"][i] for i in range(len(existing["dates"]))}
    fetched = skipped = done = 0
    while d >= start:
        ds = d.strftime("%Y-%m-%d")
        if d.weekday() < 5:
            rates = C.fetch_spot_rates_chinabond(ds, csz="1")
            if rates:
                dtr[ds] = [rates.get(t) for t in C.ALL_TERMS]
                dm750[ds] = [None] * 50
                dm60[ds] = [None] * 50
                fetched += 1
            else:
                skipped += 1
            done += 1
            if done % 100 == 0:
                _save_spot(C.DATA_FILE, dtr, dm750, dm60)
                print(f"  ...{ds} 抓 {fetched} / 跳 {skipped}")
            time.sleep(SLEEP)
        d -= timedelta(days=1)
    _save_spot(C.DATA_FILE, dtr, dm750, dm60)
    print(f"  ✅ 国债即期 回填 +{fetched}, 跳过 {skipped}")


def backfill_searchyc(name, curve_id, qxll, file):
    print("\n" + "=" * 50)
    print(f"  [{name}] 历史回填")
    print("=" * 50)
    existing = C.load_existing(file)
    if not existing["dates"]:
        print("  无现有数据，跳过"); return
    first = existing["dates"][0]
    print(f"  当前最早 {first} → 向 {START} 回填")
    start = datetime.strptime(START, "%Y-%m-%d")
    d = datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)
    dtr = {existing["dates"][i]: existing["rows"][i] for i in range(len(existing["dates"]))}
    fetched = skipped = done = 0
    while d >= start:
        ds = d.strftime("%Y-%m-%d")
        if d.weekday() < 5:
            rates = C.fetch_searchyc_rates(curve_id, qxll, ds, name)
            if rates:
                dtr[ds] = [rates.get(t) for t in C.ALL_TERMS]
                fetched += 1
            else:
                skipped += 1
            done += 1
            if done % 100 == 0:
                sd = sorted(dtr.keys())
                C.save_json(file, {"dates": sd, "terms": C.ALL_TERMS, "rows": [dtr[x] for x in sd]})
                print(f"  ...{ds} 抓 {fetched} / 跳 {skipped}")
            time.sleep(SLEEP)
        d -= timedelta(days=1)
    sd = sorted(dtr.keys())
    C.save_json(file, {"dates": sd, "terms": C.ALL_TERMS, "rows": [dtr[x] for x in sd]})
    print(f"  ✅ {name} 回填 +{fetched}, 跳过 {skipped}")


if __name__ == "__main__":
    t0 = time.time()
    backfill_gov_spot()
    backfill_searchyc("国债到期", C.GOV_CURVE_ID, "0", C.GOV_YTM_FILE)
    backfill_searchyc("国开即期", C.CDB_CURVE_ID, "1", C.CDB_DATA_FILE)
    backfill_searchyc("国开到期", C.CDB_CURVE_ID, "0", C.CDB_YTM_FILE)
    print(f"\n🎉 全部回填完成，耗时 {(time.time()-t0)/60:.1f} 分钟")
