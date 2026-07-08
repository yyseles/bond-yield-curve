#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions CI 数据更新脚本
每天自动从中债网抓取最新国债即期利率，更新 data.json
数据源: https://yield.chinabond.com.cn/cbweb-mn/yc/bxjInit (csz=1, 即期利率)
通过 bxjDownload 接口下载完整 XLSX, 包含 0~50Y 每5天一个数据点
"""
import json
import os
import tempfile
from datetime import datetime, date, timedelta

import requests
from openpyxl import load_workbook

DATA_FILE = "data.json"
CHINABOND_DOWNLOAD_URL = "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjDownload"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yc/bxjInit?locale=zh_CN",
}

# 关键期限 1Y ~ 50Y (整数年)
ALL_TERMS = [f"{i}Y" for i in range(1, 51)]

# 从这天起重新用中债网数据覆盖（修复 chinamoney 数据偏差）
BACKFILL_START = "2026-06-30"


def fetch_spot_rates_chinabond(query_date: str) -> dict:
    """
    从中债网 bxjDownload 接口下载 XLSX，提取整数年即期利率。
    返回 {"1Y": rate, "2Y": rate, ... "50Y": rate} 或空字典（非交易日/数据未发布）
    """
    params = {
        "gzr": query_date,
        "csz": "1",
        "locale": "zh_CN",
    }
    try:
        r = requests.post(CHINABOND_DOWNLOAD_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()

        # 写入临时文件（GitHub Actions 是 Linux，无文件锁问题）
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
        print(f"  {query_date}: 请求失败 - {e}")
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
    """保存 data.json"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def main():
    print("=" * 50)
    print("  国债即期利率 · CI 自动更新 (中债网)")
    print("=" * 50)

    existing = load_existing_data()
    print(f"现有数据: {len(existing['dates'])} 条")

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    # 需要抓取的日期范围
    if existing["dates"]:
        last_date = existing["dates"][-1]
    else:
        last_date = "2020-01-01"

    # 确定起始日期: BACKFILL_START 和现有最后日期的较早者
    fetch_start = min(BACKFILL_START, last_date)
    print(f"抓取范围: {fetch_start} → {today_str}")

    # 逐日抓取
    all_new = {}
    current = datetime.strptime(fetch_start, "%Y-%m-%d")
    end = datetime.strptime(today_str, "%Y-%m-%d")

    skipped = 0
    fetched = 0
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        # 跳过周末（但周一到周五都尝试）
        if current.weekday() < 5:
            rates = fetch_spot_rates_chinabond(ds)
            if rates:
                all_new[ds] = rates
                fetched += 1
                if fetched <= 3 or fetched % 10 == 0:
                    print(f"  ✓ {ds}: {len(rates)} 个期限")
            else:
                skipped += 1
        current += timedelta(days=1)

    if not all_new:
        print("\n没有获取到新数据")
        return

    print(f"\n获取: {fetched} 个交易日, 跳过/无数据: {skipped} 天")

    # 合并数据
    date_to_row = {}
    for i, d in enumerate(existing["dates"]):
        date_to_row[d] = existing["rows"][i]

    new_count = 0
    update_count = 0
    for d in sorted(all_new.keys()):
        rates = all_new[d]
        row = [rates.get(t) for t in ALL_TERMS]
        if d in date_to_row:
            date_to_row[d] = row
            update_count += 1
        else:
            date_to_row[d] = row
            new_count += 1

    sorted_dates = sorted(date_to_row.keys())
    sorted_rows = [date_to_row[d] for d in sorted_dates]

    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_data(output)

    print(f"\n更新完成: 新增 {new_count} 条, 修正 {update_count} 条")
    print(f"总计: {len(sorted_dates)} 条, {sorted_dates[0]} ~ {sorted_dates[-1]}")


if __name__ == "__main__":
    main()
