#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions CI 数据更新脚本
每天自动从中债网抓取最新利率曲线数据，更新 4 个数据文件 + 1 个摘要文件

四条曲线：
  data.json        - 国债即期 (bxjDownload, csz=1)
  data_cdb.json    - 国开债即期 (searchYc, qxll=1)
  data_gov_ytm.json - 国债到期 (searchYc, qxll=0)
  data_cdb_ytm.json - 国开债到期 (searchYc, qxll=0)
summary.json      - 四条曲线最新关键期限摘要（供仪表盘秒开）

四条曲线独立抓取，互不影响：
- 任一条失败不影响其他
- 仅抓取缺失的新日期
- 原子写入：写临时文件后 rename，防止半成品
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, date, timedelta, timezone

import requests
from openpyxl import load_workbook

DATA_FILE = "data.json"
CDB_DATA_FILE = "data_cdb.json"
GOV_YTM_FILE = "data_gov_ytm.json"
CDB_YTM_FILE = "data_cdb_ytm.json"
SUMMARY_FILE = "summary.json"

CHINABOND_DOWNLOAD_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjDownload"
SEARCHYC_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/searchYc"

GOV_CURVE_ID = "2c9081e50a2f9606010a3068cae70001"
CDB_CURVE_ID = "8a8b2ca037a7ca910137bfaa94fa5057"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjInit?locale=zh_CN",
}
SEARCHYC_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
    "Content-Type": "application/x-www-form-urlencoded",
}

ALL_TERMS = [f"{i}Y" for i in range(1, 51)]
SUMMARY_TERMS = ["1Y", "5Y", "10Y", "20Y", "30Y"]

BJ_TZ = timezone(timedelta(hours=8))
MAX_RETRIES = 3
RETRY_DELAY = 5


def now_beijing() -> date:
    return datetime.now(BJ_TZ).date()


# ================================================================
# 国债即期利率 (bxjDownload, XLSX)
# ================================================================

def fetch_spot_rates_chinabond(query_date: str) -> dict:
    params = {"gzr": query_date, "csz": "1", "locale": "zh_CN"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                CHINABOND_DOWNLOAD_URL, params=params, headers=HEADERS, timeout=30
            )
            r.raise_for_status()

            if len(r.content) < 200:
                if attempt < MAX_RETRIES:
                    print(f"  {query_date}: 响应过短({len(r.content)}B)，第{attempt}次重试...")
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"  {query_date}: 无数据 (非交易日或未发布)")
                return {}

            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(r.content)
                tmp_path = tmp.name

            try:
                wb = load_workbook(tmp_path)
                ws = wb.active
                data = {}
                for row in ws.iter_rows(min_row=2, values_only=True):
                    term_val = row[1]
                    rate_val = row[2]
                    if term_val is not None and rate_val is not None:
                        data[float(term_val)] = float(rate_val)
                wb.close()
            finally:
                os.unlink(tmp_path)

            result = {}
            for y in range(1, 51):
                val = data.get(float(y))
                if val is not None:
                    result[f"{y}Y"] = round(val, 8)

            if not result:
                print(f"  {query_date}: 无数据 (非交易日或未发布)")
                return {}

            return result

        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"  {query_date}: 请求失败({e})，第{attempt}次重试...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  {query_date}: 请求失败 - {e}")
                return {}

    return {}


# ================================================================
# searchYc 通用抓取 (国开债即期/到期, 国债到期)
# ================================================================

def fetch_searchyc_rates(curve_id: str, qxll: str, query_date: str, label: str = "") -> dict:
    params = {
        "xyzSelect": "txy",
        "workTimes": query_date,
        "dxbj": "0",
        "qxll": qxll,
        "yqqxN": "N",
        "yqqxK": "K",
        "ycDefIds": curve_id,
        "wrjxCBFlag": "0",
        "locale": "zh_CN",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                SEARCHYC_URL, data=params, headers=SEARCHYC_HEADERS, timeout=30
            )
            r.raise_for_status()
            data = r.json()

            if not data or not isinstance(data, list):
                if attempt < MAX_RETRIES:
                    print(f"  [{label}] {query_date}: 返回空，第{attempt}次重试...")
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"  [{label}] {query_date}: 无数据")
                return {}

            series = data[0].get("seriesData", [])
            result = {}
            for tenor, val in series:
                if abs(tenor - round(tenor)) < 1e-6 and 1 <= tenor <= 50:
                    result[f"{int(tenor)}Y"] = round(val, 8)

            if not result:
                print(f"  [{label}] {query_date}: 无整数年限数据")
                return {}

            return result

        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"  [{label}] {query_date}: 请求失败({e})，第{attempt}次重试...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [{label}] {query_date}: 请求失败 - {e}")
                return {}

    return {}


# ================================================================
# 通用文件读写
# ================================================================

def load_existing(filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {"dates": [], "terms": ALL_TERMS, "rows": []}
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if len(data.get("terms", [])) < 50:
        data["terms"] = ALL_TERMS
    return data


def save_json(filepath: str, data: dict):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, filepath)


# ================================================================
# 更新函数
# ================================================================

def update_gov_bond(today_str: str):
    print("\n" + "-" * 40)
    print("  [国债即期] 开始更新")
    print("-" * 40)

    existing = load_existing(DATA_FILE)
    print(f"  现有数据: {len(existing['dates'])} 条")

    if existing["dates"]:
        last_date = existing["dates"][-1]
        fetch_start = (
            datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
    else:
        fetch_start = "2020-01-01"

    print(f"  抓取范围: {fetch_start} → {today_str}")

    all_new = {}
    current = datetime.strptime(fetch_start, "%Y-%m-%d")
    end = datetime.strptime(today_str, "%Y-%m-%d")
    fetched = 0
    skipped = 0

    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        if current.weekday() < 5:
            rates = fetch_spot_rates_chinabond(ds)
            if rates:
                all_new[ds] = rates
                fetched += 1
                print(f"  ✓ {ds}: {len(rates)} 个期限")
            else:
                skipped += 1
        current += timedelta(days=1)

    print(f"  获取: {fetched} 个交易日, 跳过/无数据: {skipped} 天")

    if not all_new:
        print("  ⚠ [国债即期] 没有获取到新数据")
        return False

    date_to_row = {}
    for i, d in enumerate(existing["dates"]):
        date_to_row[d] = existing["rows"][i]

    new_count = 0
    update_count = 0
    for d in sorted(all_new.keys()):
        row = [all_new[d].get(t) for t in ALL_TERMS]
        if d in date_to_row:
            date_to_row[d] = row
            update_count += 1
        else:
            date_to_row[d] = row
            new_count += 1

    sorted_dates = sorted(date_to_row.keys())
    sorted_rows = [date_to_row[d] for d in sorted_dates]

    if len(sorted_dates) < len(existing["dates"]):
        print(f"  ⚠ [国债即期] 数据条数减少，放弃更新")
        return False

    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_json(DATA_FILE, output)

    print(f"  ✅ [国债即期] 新增 {new_count} 条, 修正 {update_count} 条, 总计 {len(sorted_dates)} 条")
    return True


def update_searchyc_bond(name: str, curve_id: str, qxll: str, data_file: str, today_str: str):
    print("\n" + "-" * 40)
    print(f"  [{name}] 开始更新")
    print("-" * 40)

    existing = load_existing(data_file)
    print(f"  现有数据: {len(existing['dates'])} 条")

    if existing["dates"]:
        last_date = existing["dates"][-1]
        fetch_start = (
            datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
    else:
        fetch_start = "2020-01-01"

    print(f"  抓取范围: {fetch_start} → {today_str}")

    all_new = {}
    current = datetime.strptime(fetch_start, "%Y-%m-%d")
    end = datetime.strptime(today_str, "%Y-%m-%d")
    fetched = 0
    skipped = 0

    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        if current.weekday() < 5:
            rates = fetch_searchyc_rates(curve_id, qxll, ds, name)
            if rates:
                all_new[ds] = rates
                fetched += 1
                print(f"  ✓ [{name}] {ds}: {len(rates)} 个期限")
            else:
                skipped += 1
        current += timedelta(days=1)

    print(f"  获取: {fetched} 个交易日, 跳过/无数据: {skipped} 天")

    if not all_new:
        print(f"  ⚠ [{name}] 没有获取到新数据")
        return False

    date_to_row = {}
    for i, d in enumerate(existing["dates"]):
        date_to_row[d] = existing["rows"][i]

    new_count = 0
    update_count = 0
    for d in sorted(all_new.keys()):
        row = [all_new[d].get(t) for t in ALL_TERMS]
        if d in date_to_row:
            date_to_row[d] = row
            update_count += 1
        else:
            date_to_row[d] = row
            new_count += 1

    sorted_dates = sorted(date_to_row.keys())
    sorted_rows = [date_to_row[d] for d in sorted_dates]

    if len(sorted_dates) < len(existing["dates"]):
        print(f"  ⚠ [{name}] 数据条数减少，放弃更新")
        return False

    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_json(data_file, output)

    print(f"  ✅ [{name}] 新增 {new_count} 条, 修正 {update_count} 条, 总计 {len(sorted_dates)} 条")
    return True


# ================================================================
# summary.json 生成
# ================================================================

def generate_summary():
    print("\n" + "-" * 40)
    print("  [summary] 生成摘要文件")
    print("-" * 40)

    curves_config = [
        ("gov_spot", DATA_FILE, "国债即期"),
        ("gov_ytm", GOV_YTM_FILE, "国债到期"),
        ("cdb_spot", CDB_DATA_FILE, "国开债即期"),
        ("cdb_ytm", CDB_YTM_FILE, "国开债到期"),
    ]

    summary = {"curves": {}}
    all_dates = set()

    for key, filepath, label in curves_config:
        if not os.path.exists(filepath):
            print(f"  [{label}] 文件不存在，跳过")
            continue

        data = load_existing(filepath)
        if not data["dates"]:
            print(f"  [{label}] 无数据，跳过")
            continue

        latest_date = data["dates"][-1]
        latest_row = data["rows"][-1]
        terms = data["terms"]

        # 获取前一个交易日的数据用于计算变动
        prev_row = None
        if len(data["dates"]) >= 2:
            prev_date = data["dates"][-2]
            prev_row = data["rows"][-2]

        terms_data = {}
        for term in SUMMARY_TERMS:
            if term in terms:
                idx = terms.index(term)
                val = latest_row[idx] if idx < len(latest_row) else None
                prev_val = prev_row[idx] if prev_row and idx < len(prev_row) else None
                change = None
                if val is not None and prev_val is not None:
                    change = round(val - prev_val, 4)
                terms_data[term] = {"value": val, "change": change}

        summary["curves"][key] = {
            "name": label,
            "date": latest_date,
            "terms": terms_data,
        }
        all_dates.add(latest_date)
        print(f"  [{label}] 最新日期: {latest_date}")

    # 用最新交易日作为整体日期
    summary["date"] = max(all_dates) if all_dates else ""

    save_json(SUMMARY_FILE, summary)
    print(f"  ✅ [summary] 生成完成, 日期: {summary['date']}")


# ================================================================
# 主函数
# ================================================================

def main():
    print("=" * 55)
    print("  利率曲线 · CI 自动更新 (四曲线)")
    print(f"  北京时间: {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    today_bj = now_beijing()
    today_str = today_bj.strftime("%Y-%m-%d")

    # 四条曲线独立更新，互不影响
    gov_ok = update_gov_bond(today_str)
    cdb_ok = update_searchyc_bond("国开债即期", CDB_CURVE_ID, "1", CDB_DATA_FILE, today_str)
    gov_ytm_ok = update_searchyc_bond("国债到期", GOV_CURVE_ID, "0", GOV_YTM_FILE, today_str)
    cdb_ytm_ok = update_searchyc_bond("国开债到期", CDB_CURVE_ID, "0", CDB_YTM_FILE, today_str)

    # 生成 summary.json（只要至少一条曲线有数据就生成）
    generate_summary()

    print("\n" + "=" * 55)
    results = []
    for ok, label in [(gov_ok, "国债即期"), (cdb_ok, "国开即期"), (gov_ytm_ok, "国债到期"), (cdb_ytm_ok, "国开到期")]:
        results.append(f"{label} {'✅' if ok else '⚠'}")
    print("  汇总: " + " | ".join(results))
    print("=" * 55)

    if not any([gov_ok, cdb_ok, gov_ytm_ok, cdb_ytm_ok]):
        print("\n⚠ 四条曲线均无新数据")
        sys.exit(0)


if __name__ == "__main__":
    main()
