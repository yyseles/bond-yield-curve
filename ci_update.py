#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions CI 数据更新脚本
每天自动从中国货币网抓取最新国债即期利率，更新 data.json
"""
import json
import os
import sys
from datetime import datetime, date, timedelta
from collections import OrderedDict

import requests

DATA_FILE = "data.json"
CHINAMONEY_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/ClsYldCurvHis"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 国债标准期限（整数年 1Y-50Y）
ALL_TERMS = [f"{i}Y" for i in range(1, 51)]


def fetch_spot_rates(start_date: str, end_date: str) -> dict:
    """从中国货币网抓取即期利率，返回 {date: {term: rate}}"""
    all_records = []
    page = 1
    while True:
        params = {
            "lang": "CN",
            "reference": "1,2,3",
            "bondType": "CYCC000",
            "startDate": start_date,
            "endDate": end_date,
            "termId": "1",
            "pageNum": str(page),
            "pageSize": "50",
        }
        try:
            r = requests.get(CHINAMONEY_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            records = data.get("records", [])
            all_records.extend(records)
            print(f"  Page {page}: {len(records)} records")
            if len(records) < 50:
                break
            page += 1
        except Exception as e:
            print(f"  Page {page} error: {e}")
            break

    result = {}
    for rec in all_records:
        d = rec["newDateValueCN"]
        term = float(rec["yearTermStr"])
        if term != int(term):
            continue
        term_key = f"{int(term)}Y"
        if d not in result:
            result[d] = {}
        result[d][term_key] = float(rec["currentYieldStr"])

    return result


def load_existing_data() -> dict:
    """加载现有 data.json"""
    if not os.path.exists(DATA_FILE):
        return {"dates": [], "terms": ALL_TERMS, "rows": []}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 确保 terms 完整
    if len(data.get("terms", [])) < 50:
        data["terms"] = ALL_TERMS

    return data


def save_data(data: dict):
    """保存 data.json"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def main():
    print("=" * 50)
    print("  国债即期利率 · CI 自动更新")
    print("=" * 50)

    # 加载现有数据
    existing = load_existing_data()
    print(f"现有数据: {len(existing['dates'])} 条")

    # 确定抓取范围
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    if existing["dates"]:
        last_date = existing["dates"][-1]
        start_date = last_date
    else:
        start_date = today.replace(year=today.year - 1, month=1, day=1).strftime("%Y-%m-%d")

    print(f"抓取范围: {start_date} → {today_str}")

    # 分月抓取
    all_new = {}
    cursor = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(today_str, "%Y-%m-%d")

    while cursor <= end_dt:
        if cursor.year == end_dt.year and cursor.month == end_dt.month:
            seg_end = end_dt
        else:
            if cursor.month == 12:
                next_month = datetime(cursor.year + 1, 1, 1)
            else:
                next_month = datetime(cursor.year, cursor.month + 1, 1)
            seg_end = next_month - timedelta(days=1)
            if seg_end > end_dt:
                seg_end = end_dt

        seg_start_str = cursor.strftime("%Y-%m-%d")
        seg_end_str = seg_end.strftime("%Y-%m-%d")

        if seg_start_str <= seg_end_str:
            print(f"抓取 {seg_start_str} ~ {seg_end_str} ...")
            try:
                data = fetch_spot_rates(seg_start_str, seg_end_str)
                print(f"  获取 {len(data)} 个交易日: {sorted(data.keys())}")
                all_new.update(data)
            except Exception as e:
                print(f"  失败: {e}")

        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1)

    if not all_new:
        print("\n没有获取到新数据（可能已是最新或接口不可用）")
        return

    # 合并数据：建立日期→行的映射
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

    # 按日期排序重建
    sorted_dates = sorted(date_to_row.keys())
    sorted_rows = [date_to_row[d] for d in sorted_dates]

    # 保存
    output = {"dates": sorted_dates, "terms": ALL_TERMS, "rows": sorted_rows}
    save_data(output)

    print(f"\n更新完成: 新增 {new_count} 条, 更新 {update_count} 条")
    print(f"总计: {len(sorted_dates)} 条, {sorted_dates[0]} ~ {sorted_dates[-1]}")


if __name__ == "__main__":
    main()
