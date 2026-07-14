#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions CI 数据更新脚本
每天自动从中债网抓取最新国债和国开债即期利率，更新 data.json 和 data_cdb.json

国债数据源: bxjDownload 接口 (XLSX, 含 0~50Y)
国开债数据源: searchYc 接口 (JSON, 含 0~50Y)

两种债券独立抓取，互不影响：
- 国债失败不影响国开债，反之亦然
- 仅抓取缺失的新日期，避免重复抓取
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
CHINABOND_DOWNLOAD_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjDownload"
CDB_SEARCH_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/searchYc"
CDB_CURVE_ID = "8a8b2ca037a7ca910137bfaa94fa5057"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjInit?locale=zh_CN",
}
CDB_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
    "Content-Type": "application/x-www-form-urlencoded",
}

# 关键期限 1Y ~ 50Y (整数年)
ALL_TERMS = [f"{i}Y" for i in range(1, 51)]

# 北京时区 UTC+8
BJ_TZ = timezone(timedelta(hours=8))

MAX_RETRIES = 3
RETRY_DELAY = 5  # 秒


def now_beijing() -> date:
    """返回北京时间今天的日期"""
    return datetime.now(BJ_TZ).date()


def fetch_spot_rates_chinabond(query_date: str) -> dict:
    """
    从中债网 bxjDownload 接口下载 XLSX，提取整数年即期利率。
    返回 {"1Y": rate, "2Y": rate, ... "50Y": rate} 或空字典（非交易日/数据未发布）
    带重试机制。
    """
    params = {
        "gzr": query_date,
        "csz": "1",
        "locale": "zh_CN",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                CHINABOND_DOWNLOAD_URL, params=params, headers=HEADERS, timeout=30
            )
            r.raise_for_status()

            # 检查响应是否为有效 Excel（中债网无数据时可能返回小体积非Excel内容）
            if len(r.content) < 200:
                if attempt < MAX_RETRIES:
                    print(f"  {query_date}: 响应过短({len(r.content)}B)，第{attempt}次重试...")
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"  {query_date}: 无数据 (非交易日或未发布)")
                return {}

            # 写入临时文件
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(r.content)
                tmp_path = tmp.name

            try:
                wb = load_workbook(tmp_path)
                ws = wb.active

                data = {}
                for row in ws.iter_rows(min_row=2, values_only=True):
                    term_val = row[1]  # 标准期限(年)
                    rate_val = row[2]  # 平均值(%)
                    if term_val is not None and rate_val is not None:
                        data[float(term_val)] = float(rate_val)

                wb.close()
            finally:
                os.unlink(tmp_path)

            # 提取整数年
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


def fetch_cdb_spot_rates(query_date: str) -> dict:
    """
    从中债 searchYc 接口抓取国开债即期利率。
    返回 {"1Y": rate, ...} 或空字典。
    """
    params = {
        "xyzSelect": "txy",
        "workTimes": query_date,
        "dxbj": "0",
        "qxll": "1",
        "yqqxN": "N",
        "yqqxK": "K",
        "ycDefIds": CDB_CURVE_ID,
        "wrjxCBFlag": "0",
        "locale": "zh_CN",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                CDB_SEARCH_URL, data=params, headers=CDB_HEADERS, timeout=30
            )
            r.raise_for_status()
            data = r.json()

            if not data or not isinstance(data, list):
                if attempt < MAX_RETRIES:
                    print(f"  [CDB] {query_date}: 返回空，第{attempt}次重试...")
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"  [CDB] {query_date}: 无数据 (非交易日或未发布)")
                return {}

            series = data[0].get("seriesData", [])
            result = {}
            for tenor, val in series:
                if abs(tenor - round(tenor)) < 1e-6 and 1 <= tenor <= 50:
                    result[f"{int(tenor)}Y"] = round(val, 8)

            if not result:
                print(f"  [CDB] {query_date}: 无整数年限数据")
                return {}

            return result

        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"  [CDB] {query_date}: 请求失败({e})，第{attempt}次重试...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [CDB] {query_date}: 请求失败 - {e}")
                return {}

    return {}


def load_existing_data() -> dict:
    """加载现有 data.json"""
    if not os.path.exists(DATA_FILE):
        return {"dates": [], "terms": ALL_TERMS, "rows": []}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if len(data.get("terms", [])) < 50:
        data["terms"] = ALL_TERMS

    return data


def save_data(data: dict):
    """保存 data.json（原子写入）"""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def load_existing_cdb_data() -> dict:
    """加载现有 data_cdb.json"""
    if not os.path.exists(CDB_DATA_FILE):
        return {"dates": [], "terms": ALL_TERMS, "rows": []}
    with open(CDB_DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if len(data.get("terms", [])) < 50:
        data["terms"] = ALL_TERMS
    return data


def save_cdb_data(data: dict):
    """保存 data_cdb.json（原子写入）"""
    tmp = CDB_DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CDB_DATA_FILE)


def update_gov_bond(today_str: str):
    """更新国债数据，失败时保留原文件不覆盖"""
    print("\n" + "-" * 40)
    print("  [国债] 开始更新")
    print("-" * 40)

    existing = load_existing_data()
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
        print("  ⚠ [国债] 没有获取到新数据")
        return False

    # 合并
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

    # 校验：新数据不应导致总条数减少
    if len(sorted_dates) < len(existing["dates"]):
        print(f"  ⚠ [国债] 数据条数减少({len(existing['dates'])}→{len(sorted_dates)})，放弃更新")
        return False

    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_data(output)

    print(f"  ✅ [国债] 新增 {new_count} 条, 修正 {update_count} 条")
    print(f"     总计: {len(sorted_dates)} 条, {sorted_dates[0]} ~ {sorted_dates[-1]}")
    return True


def update_cdb_bond(today_str: str):
    """更新国开债数据，失败时保留原文件不覆盖"""
    print("\n" + "-" * 40)
    print("  [国开债] 开始更新")
    print("-" * 40)

    existing = load_existing_cdb_data()
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
            rates = fetch_cdb_spot_rates(ds)
            if rates:
                all_new[ds] = rates
                fetched += 1
                print(f"  ✓ [CDB] {ds}: {len(rates)} 个期限")
            else:
                skipped += 1
        current += timedelta(days=1)

    print(f"  获取: {fetched} 个交易日, 跳过/无数据: {skipped} 天")

    if not all_new:
        print("  ⚠ [国开债] 没有获取到新数据")
        return False

    # 合并
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

    # 校验
    if len(sorted_dates) < len(existing["dates"]):
        print(f"  ⚠ [国开债] 数据条数减少({len(existing['dates'])}→{len(sorted_dates)})，放弃更新")
        return False

    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_cdb_data(output)

    print(f"  ✅ [国开债] 新增 {new_count} 条, 修正 {update_count} 条")
    print(f"     总计: {len(sorted_dates)} 条, {sorted_dates[0]} ~ {sorted_dates[-1]}")
    return True


def main():
    print("=" * 55)
    print("  利率曲线 · CI 自动更新 (国债 + 国开债)")
    print(f"  北京时间: {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    today_bj = now_beijing()
    today_str = today_bj.strftime("%Y-%m-%d")

    # 两种债券独立更新，互不影响
    gov_ok = update_gov_bond(today_str)
    cdb_ok = update_cdb_bond(today_str)

    print("\n" + "=" * 55)
    print(f"  汇总: 国债 {'✅' if gov_ok else '⚠'} | 国开债 {'✅' if cdb_ok else '⚠'}")
    print("=" * 55)

    # 只有两者都失败才退出非0
    if not gov_ok and not cdb_ok:
        print("\n⚠ 国债和国开债均无新数据")
        sys.exit(0)


if __name__ == "__main__":
    main()
