#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_ins_bonds.py
从 中国货币网(chinamoney)「债券信息综合查询」抓取保险公司发行的资本补充债与
无固定期限资本债券(永续债)全量数据，输出 ins_bonds.json。

数据源:
  - 列表: AKShare bond_info_cm (接口 BondMarketInfoList2), 按 债券类型 × 发行年份 分页拉全
  - 详情: 直接调 BondDetailInfo(bondDefinedCode=查询代码), 补全 票面利率/起息日/到期日/发行量/评级/含权

处理逻辑:
  - 永续债类型(无固定期限资本债券)含银行债, 按发行人名过滤只留保险公司
  - 行业(产/寿/再保/集团)按发行人名关键字推断
  - 状态(存续/已赎回/已到期)按 起息日+5年(call) 与 到期兑付日 相对运行日判定
    (赎回只改状态, 不动发行总额 —— 由前端/汇总时按 issueYear 聚合保证)

用法:
  python fetch_ins_bonds.py              # 抓全量
  python fetch_ins_bonds.py --year 2025  # 只抓某年(调试)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
DATA_FILE = __import__("os").path.join(HERE, "ins_bonds.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
}
DETAIL_HEADERS = {
    **HEADERS,
    "host": "www.chinamoney.com.cn",
    "origin": "https://www.chinamoney.com.cn",
    "referer": "https://www.chinamoney.com.cn/chinese/zqjc/",
}

LIST_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondMarketInfoList2"
DETAIL_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bond-md/BondDetailInfo"

BOND_TYPES = ["保险公司资本补充债", "无固定期限资本债券"]
START_YEAR = 2012  # 资本补充债最早 2015 前后, 永续 2019 前后; 宽一点无妨

# ---------- 工具 ----------

def _norm_date(s):
    if not s or s in ("---", "0", "None", "null"):
        return None
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _to_float(s):
    if s is None or s in ("---", "", "None"):
        return None
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def is_insurance(issuer: str) -> bool:
    if not issuer:
        return False
    # 排除银行类(永续债类型混有大量银行)
    if re.search(r"银行|商行|农商|信用社", issuer):
        return False
    return bool(re.search(r"保险|人寿|财险|产险|再保险|养老|健康|相互|信保", issuer))


def infer_industry(issuer: str) -> str:
    if not issuer:
        return "其他"
    if "再保险" in issuer:
        return "再保"
    if re.search(r"财险|财产|产险", issuer):
        return "产险"
    if re.search(r"集团|控股", issuer):
        return "集团"
    return "寿险"


def derive_call_date(value_date: date, bond_period: str):
    """含权债的 call 日: 从 '5+5年' / '5+N年' 取 '+' 前的年数, 加到起息日。"""
    if not value_date or not bond_period:
        return None
    m = re.search(r"(\d+)\s*\+", str(bond_period))
    if not m:
        return None
    try:
        years = int(m.group(1))
    except ValueError:
        return None
    try:
        return value_date.replace(year=value_date.year + years)
    except ValueError:  # 闰年 02-29
        return value_date + timedelta(days=365 * years)


def compute_status(value_date, mrty_date, call_date, exercise_flag, is_perpetual=False):
    today = date.today()
    if is_perpetual:
        # 永续债无真实到期日(chinamoney 的 mrtyDate 即首个赎回日), 只判 存续/已赎回
        if call_date and call_date < today:
            return "已赎回"
        return "存续"
    if mrty_date and mrty_date < today:
        return "已到期"
    if exercise_flag == "是" and call_date and call_date < today and \
       (mrty_date is None or call_date <= mrty_date):
        return "已赎回"
    return "存续"


# ---------- 抓取 ----------

def fetch_list(bond_type: str, year: str, retries=5):
    """返回该类型该年份的全部债券摘要列表(含 查询代码)。带重试(限流时返回非JSON)。"""
    import akshare as ak
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = ak.bond_info_cm(bond_type=bond_type, issue_year=year)
            return df.to_dict("records")
        except Exception as e:  # noqa
            last_err = e
            time.sleep(2.0 * attempt)
    sys.stderr.write(f"  [warn] list fail {bond_type} {year}: {last_err}\n")
    return []


def fetch_detail(bond_defined_code: str, retries=4):
    """直接调 BondDetailInfo, 返回 bondBaseInfo dict; 失败返回 None。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(DETAIL_URL, data={"bondDefinedCode": bond_defined_code},
                              headers=DETAIL_HEADERS, timeout=30)
            j = r.json()
            info = j.get("data", {}).get("bondBaseInfo")
            if info:
                return info
        except Exception as e:  # noqa
            last_err = e
        time.sleep(1.5 * attempt)
    if last_err:
        sys.stderr.write(f"  [warn] detail fail {bond_defined_code}: {last_err}\n")
    return None


def build_record(list_row, info):
    enty = info.get("entyFullName") or list_row.get("发行人/受托机构") or ""
    bond_period = info.get("bondPeriod") or ""
    bt = info.get("bondType") or list_row.get("债券类型") or ""
    bond_type_norm = "永续债" if "无固定期限" in bt else ("资本补充债" if "资本补充" in bt else bt)
    value_date = _norm_date(info.get("frstValueDate"))
    mrty_date = _norm_date(info.get("mrtyDate"))
    call_date = derive_call_date(value_date, bond_period)
    exercise_flag = info.get("exerciseInfoFlag") or "否"
    # 主体/债项评级: creditRateEntyList[0].creditSubjectRating 形如 "AA+/AAA"
    rating_str = ""
    crl = info.get("creditRateEntyList") or []
    if crl and isinstance(crl, list):
        rating_str = crl[0].get("creditSubjectRating") or ""
    status = compute_status(value_date, mrty_date, call_date, exercise_flag,
                             is_perpetual=(bond_type_norm == "永续债"))
    issue_date = _norm_date(info.get("issueDate")) or _norm_date(list_row.get("发行日期"))
    rec = {
        "bondDefinedCode": info.get("bondDefinedCode") or list_row.get("查询代码"),
        "issuer": enty,
        "bondShort": info.get("bondName") or list_row.get("债券简称"),
        "bondFull": info.get("bondFullName") or "",
        "bondCode": info.get("bondCode") or list_row.get("债券代码"),
        "bondType": bond_type_norm,
        "industry": infer_industry(enty),
        "issueDate": issue_date.isoformat() if issue_date else (list_row.get("发行日期") or ""),
        "valueDate": value_date.isoformat() if value_date else "",
        "mrtyDate": mrty_date.isoformat() if mrty_date else "",
        "bondPeriod": bond_period,
        "planAmnt": _to_float(info.get("plndIssueAmnt")),
        "issueAmnt": _to_float(info.get("issueAmnt")),
        "couponRate": _to_float(info.get("parCouponRate")),
        "couponType": info.get("couponType") or "",
        "couponFrqncy": info.get("couponFrqncy") or "",
        "debtRating": list_row.get("最新债项评级") or "",
        "ratingStr": rating_str,
        "exerciseFlag": exercise_flag,
        "callDate": call_date.isoformat() if call_date else "",
        "status": status,
    }
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=None, help="只抓某年(调试)")
    ap.add_argument("--type", default=None, help="只抓某类型(资本补充债/永续债)，合并进已有 json")
    ap.add_argument("--sleep", type=float, default=0.6, help="详情请求间隔(秒)")
    ap.add_argument("--year-sleep", type=float, default=1.5, help="每年列表请求间隔(秒)")
    args = ap.parse_args()

    types = [args.type] if args.type else BOND_TYPES
    years = [args.year] if args.year else [str(y) for y in range(START_YEAR, date.today().year + 1)]

    # 始终以已有 json 为基底：按 (发行人, 债券简称) 去重，保留 Excel 来源的永续债/资本补充债，
    # chinamoney 仅补充其中缺失的新发行债，避免重复计数或把永续债覆盖丢失。
    seen = {}

    def _norm_issuer(s):
        s = str(s or "").strip()
        m = re.search(r"[（(]原", s)   # 去掉 "(原:信诚人寿…)" 曾用名后缀，避免同一公司被当两条
        return s[:m.start()].strip() if m else s

    def _key(b):
        return (_norm_issuer(b.get("issuer")), str(b.get("bondShort", "")).strip())

    if os.path.exists(DATA_FILE):
        try:
            prev = json.load(open(DATA_FILE, encoding="utf-8"))
            for b in prev.get("bonds", []):
                k = _key(b)
                if k[0] or k[1]:
                    seen[k] = b
            print(f"[merge] 载入已有 {len(seen)} 只", flush=True)
        except Exception as e:  # noqa
            sys.stderr.write(f"  [warn] load prev fail: {e}\n")

    for bt in types:
        print(f"[info] 类型={bt}", flush=True)
        for yr in years:
            rows = fetch_list(bt, yr)
            time.sleep(args.year_sleep)
            if not rows:
                continue
            for row in rows:
                issuer = row.get("发行人/受托机构") or ""
                if not is_insurance(issuer):
                    continue
                short = row.get("债券简称") or ""
                code = row.get("查询代码")
                key = (_norm_issuer(issuer), short.strip())
                if not code or key in seen:
                    continue
                info = fetch_detail(code)
                time.sleep(args.sleep)
                if not info:
                    # 详情缺失也保留列表级最小记录, 标记 status 未知
                    rec = build_record(row, {
                        "bondDefinedCode": code,
                        "entyFullName": issuer,
                        "bondName": row.get("债券简称"),
                        "bondFullName": "",
                        "bondCode": row.get("债券代码"),
                        "bondType": bt,
                        "issueDate": row.get("发行日期"),
                        "frstValueDate": "",
                        "mrtyDate": "",
                        "bondPeriod": "",
                        "plndIssueAmnt": None,
                        "issueAmnt": None,
                        "parCouponRate": None,
                        "couponType": "",
                        "couponFrqncy": "",
                        "exerciseInfoFlag": "否",
                        "creditRateEntyList": [],
                    })
                    rec["status"] = "存续"  # 无到期日信息时保守视为存续
                else:
                    rec = build_record(row, info)
                seen[key] = rec
            print(f"  {yr}: 累计 {len(seen)} 只", flush=True)
        time.sleep(3)

    bonds = list(seen.values())
    # 按发行日倒序
    bonds.sort(key=lambda r: r.get("issueDate") or "", reverse=True)

    out = {
        "generatedAt": date.today().isoformat(),
        "source": "中国货币网(chinamoney) 债券信息综合查询",
        "count": len(bonds),
        "bonds": bonds,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[done] 写出 {len(bonds)} 只 -> {DATA_FILE}", flush=True)

    # 简要统计
    from collections import Counter
    by_type = Counter(b["bondType"] for b in bonds)
    by_status = Counter(b["status"] for b in bonds)
    by_ind = Counter(b["industry"] for b in bonds)
    print("  按类型:", dict(by_type))
    print("  按状态:", dict(by_status))
    print("  按行业:", dict(by_ind))


if __name__ == "__main__":
    main()
