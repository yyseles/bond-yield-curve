#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_perp.py —— 仅补抓「保险公司永续债（无固定期限资本债券）」并合并进 ins_bonds.json。
背景：chinamoney 对本机 IP 有连接数/频率限制(421)，故请求放慢、遇 421 退避重试。
保留已有的 138 只资本补充债（按 bondDefinedCode 去重合并）。
"""
import json
import os
import re
import sys
import time
from datetime import date

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "ins_bonds.json")
LIST_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondMarketInfoList2"
DETAIL_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondDetailInfo"

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "host": "www.chinamoney.com.cn",
    "origin": "https://www.chinamoney.com.cn",
    "referer": "https://www.chinamoney.com.cn/chinese/zqjc/",
    "accept": "application/json, text/plain, */*",
}

START_YEAR = 2019
END_YEAR = date.today().year


def log(m):
    sys.stderr.write(m + "\n")
    sys.stderr.flush()


def is_insurance(issuer):
    if not issuer:
        return False
    if re.search(r"银行|商行|农商|信用社", issuer):
        return False
    return bool(re.search(r"保险|人寿|财险|产险|再保险|养老|健康|相互|信保", issuer))


def infer_industry(issuer):
    if not issuer:
        return "其他"
    if "再保险" in issuer:
        return "再保"
    if re.search(r"财险|财产|产险", issuer):
        return "产险"
    if re.search(r"集团|控股", issuer):
        return "集团"
    return "寿险"


def norm_date(s):
    if not s or s in ("---", "0", "None", "null"):
        return None
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def to_float(s):
    if s in (None, "---", "", "None"):
        return None
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def derive_call(value_date, period):
    if not value_date or not period:
        return None
    m = re.search(r"(\d+)\s*\+", str(period))
    if not m:
        return None
    try:
        y = int(m.group(1))
        return value_date.replace(year=value_date.year + y)
    except ValueError:
        return value_date
    except Exception:
        return None


def fetch_list_year(sess, year, retries=10):
    """返回该年全部记录列表；遇 421/非 JSON 退避重试。"""
    for i in range(1, retries + 1):
        try:
            r = sess.post(LIST_URL, data={
                "bondType": "无固定期限资本债券", "issueYear": str(year),
                "pageNo": 1, "pageSize": 100,
            }, headers=H, timeout=40)
            if r.status_code == 421:
                log(f"  [421] {year} 第{i}次, 退避 {8*i}s")
                time.sleep(8 * i)
                continue
            if not r.text.strip().startswith("{"):
                log(f"  [non-json] {year} 第{i}次 status={r.status_code}, 退避 {6*i}s")
                time.sleep(6 * i)
                continue
            j = r.json()
            data = (j.get("data") or {})
            rows = data.get("result") or data.get("list") or data.get("records") or []
            # 翻页：若总记录数 > pageSize
            total = data.get("total") or data.get("totalNum") or 0
            if isinstance(total, (int, float)) and total > len(rows):
                page = 2
                while len(rows) < total and page <= 50:
                    rr = sess.post(LIST_URL, data={
                        "bondType": "无固定期限资本债券", "issueYear": str(year),
                        "pageNo": page, "pageSize": 100,
                    }, headers=H, timeout=40)
                    if rr.status_code == 421:
                        time.sleep(8); continue
                    jj = rr.json().get("data") or {}
                    more = jj.get("result") or jj.get("list") or []
                    if not more:
                        break
                    rows += more
                    page += 1
                    time.sleep(2)
            return rows
        except Exception as e:
            log(f"  [err] {year} 第{i}次: {e}, 退避 {6*i}s")
            time.sleep(6 * i)
    log(f"  [fail] {year} 放弃")
    return []


def fetch_detail(sess, code, retries=5):
    for i in range(1, retries + 1):
        try:
            r = sess.post(DETAIL_URL, data={"bondDefinedCode": code}, headers=H, timeout=40)
            if r.status_code == 421:
                time.sleep(6 * i); continue
            if not r.text.strip().startswith("{"):
                time.sleep(4 * i); continue
            info = r.json().get("data", {}).get("bondBaseInfo")
            if info:
                return info
        except Exception as e:
            log(f"  [derr] {code}: {e}")
            time.sleep(4 * i)
    return None


def build_record(row, info):
    enty = (info.get("entyFullName") or row.get("发行人/受托机构") or "").strip()
    period = info.get("bondPeriod") or ""
    value_date = norm_date(info.get("frstValueDate"))
    mrty_date = norm_date(info.get("mrtyDate"))
    call_date = derive_call(value_date, period)
    ex_flag = info.get("exerciseInfoFlag") or "否"
    rating = ""
    crl = info.get("creditRateEntyList") or []
    if crl and isinstance(crl, list):
        rating = crl[0].get("creditSubjectRating") or ""
    status = "存续"
    if call_date and call_date < date.today():
        status = "已赎回"
    issue_date = norm_date(info.get("issueDate")) or norm_date(row.get("发行日期"))
    return {
        "bondDefinedCode": info.get("bondDefinedCode") or row.get("查询代码"),
        "issuer": enty,
        "bondShort": info.get("bondName") or row.get("债券简称"),
        "bondFull": info.get("bondFullName") or "",
        "bondCode": info.get("bondCode") or row.get("债券代码"),
        "bondType": "永续债",
        "industry": infer_industry(enty),
        "issueDate": issue_date.isoformat() if issue_date else (row.get("发行日期") or ""),
        "valueDate": value_date.isoformat() if value_date else "",
        "mrtyDate": mrty_date.isoformat() if mrty_date else "",
        "bondPeriod": period,
        "planAmnt": to_float(info.get("plndIssueAmnt")),
        "issueAmnt": to_float(info.get("issueAmnt")),
        "couponRate": to_float(info.get("parCouponRate")),
        "couponType": info.get("couponType") or "",
        "couponFrqncy": info.get("couponFrqncy") or "",
        "debtRating": row.get("最新债项评级") or "",
        "ratingStr": rating,
        "exerciseFlag": ex_flag,
        "callDate": call_date.isoformat() if call_date else "",
        "status": status,
    }


def main():
    sess = requests.Session()
    # 载入已有
    seen = {}
    if os.path.exists(DATA_FILE):
        prev = json.load(open(DATA_FILE, encoding="utf-8"))
        for b in prev.get("bonds", []):
            if b.get("bondDefinedCode"):
                seen[b["bondDefinedCode"]] = b
    log(f"[merge] 载入已有 {len(seen)} 只")

    for yr in range(START_YEAR, END_YEAR + 1):
        log(f"[year] {yr}")
        rows = fetch_list_year(sess, yr)
        time.sleep(3)
        if not rows:
            continue
        for row in rows:
            issuer = row.get("发行人/受托机构") or ""
            if not is_insurance(issuer):
                continue
            code = row.get("查询代码")
            if not code or code in seen:
                continue
            info = fetch_detail(sess, code)
            time.sleep(1.5)
            if not info:
                continue
            seen[code] = build_record(row, info)
        log(f"  {yr}: 累计 {len(seen)} 只")

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


if __name__ == "__main__":
    main()
