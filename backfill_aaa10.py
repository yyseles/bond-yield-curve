# -*- coding: utf-8 -*-
"""回填中债企业债收益率曲线(AAA) 10年期 即期/到期 历史数据（仅 10Y 单点）。

数据源: searchYc 接口（与 ci_update.py 同一机制），曲线 ID 2c9081e50a2f9606010a309f4af50111。
输出:
  data_aaa10_spot.json  {dates, terms:['10Y'], rows:[[即期10Y]]}
  data_aaa10_ytm.json   {dates, terms:['10Y'], rows:[[到期10Y]]}
日历: 以 data_cdb.json 的交易日为准（2008-01-02 起）。
支持断点续跑：仅抓取文件中尚无数据的日期。失败日期记录到 _aaa10_missing.json 供重跑。
"""
import json
import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/searchYc"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
    "Content-Type": "application/x-www-form-urlencoded",
}
AAA_ID = "2c9081e50a2f9606010a309f4af50111"  # 中债企业债收益率曲线(AAA)

SPOT_FILE = "data_aaa10_spot.json"
YTM_FILE = "data_aaa10_ytm.json"
MISSING_FILE = "_aaa10_missing.json"

MAX_RETRIES = 4
RETRY_DELAY = 2.0
WORKERS = 4


def fetch_10y(date, qxll):
    """抓取指定日期 10Y 利率(%)，失败重试，最终失败返回 None。"""
    params = {
        "xyzSelect": "txy", "workTimes": date, "dxbj": "0",
        "qxll": qxll, "yqqxN": "N", "yqqxK": "K",
        "ycDefIds": AAA_ID, "wrjxCBFlag": "0", "locale": "zh_CN",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(URL, data=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                time.sleep(RETRY_DELAY)
                continue
            for tenor, val in (data[0].get("seriesData") or []):
                if abs(float(tenor) - 10.0) < 1e-6:
                    return round(float(val), 6)
            return None  # 曲线存在但无 10Y 点（早期可能短端-only）
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    return "FAIL"


def load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"dates": [], "terms": ["10Y"], "rows": []}


def main():
    ref = load("data_cdb.json")["dates"]
    if not ref:
        print("data_cdb.json 无日历")
        sys.exit(1)
    spot = load(SPOT_FILE)
    ytm = load(YTM_FILE)
    have_spot = set(spot["dates"])
    have_ytm = set(ytm["dates"])
    todo = [d for d in ref if d not in have_spot or d not in have_ytm]
    print(f"日历 {len(ref)} 天 ( {ref[0]}~{ref[-1]} )，待补 {len(todo)} 天 x2 请求")

    results = {}  # date -> {'spot': v, 'ytm': v}  v=数值 或 'FAIL'/None
    done = [0]

    def work(date):
        out = {}
        if date not in have_spot:
            out["spot"] = fetch_10y(date, "1")
        if date not in have_ytm:
            out["ytm"] = fetch_10y(date, "0")
        return date, out

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, d) for d in todo]
        for fut in as_completed(futs):
            date, out = fut.result()
            results[date] = out
            done[0] += 1
            if done[0] % 100 == 0 or done[0] == len(todo):
                rate = done[0] / max(time.time() - t0, 1)
                eta = (len(todo) - done[0]) / max(rate, 0.1) / 60
                print(f"  进度 {done[0]}/{len(todo)}  ({rate:.1f} req/s, ETA {eta:.0f} min)", flush=True)

    # 合并写回（保持日期升序）
    missing = {"spot": [], "ytm": []}
    for date, out in results.items():
        if "spot" in out:
            if out["spot"] in (None, "FAIL"):
                if out["spot"] == "FAIL":
                    missing["spot"].append(date)
            else:
                have_spot.add(date)
        if "ytm" in out:
            if out["ytm"] in (None, "FAIL"):
                if out["ytm"] == "FAIL":
                    missing["ytm"].append(date)
            else:
                have_ytm.add(date)

    _rewrite(SPOT_FILE, results, "spot", have_spot)
    _rewrite(YTM_FILE, results, "ytm", have_ytm)
    with open(MISSING_FILE, "w", encoding="utf-8") as f:
        json.dump(missing, f, ensure_ascii=False)
    print(f"失败未取到: spot {len(missing['spot'])} / ytm {len(missing['ytm'])} (见 {MISSING_FILE})")
    print(f"总耗时 {(time.time()-t0)/60:.1f} 分钟")


def _rewrite(path, results, key, have_set):
    d = load(path)
    rows = dict(zip(d["dates"], d["rows"]))
    added = 0
    for date, out in results.items():
        if key in out and out[key] not in (None, "FAIL"):
            if date not in have_set:
                added += 1
            rows[date] = [out[key]]
    dates = sorted(rows.keys())
    out = {"terms": ["10Y"], "dates": dates, "rows": [rows[x] for x in dates]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"写回 {path}: +{added} 新增, 共 {len(dates)} 天 ( {dates[0]}~{dates[-1]} )" if dates else f"写回 {path}: 空")


if __name__ == "__main__":
    main()
